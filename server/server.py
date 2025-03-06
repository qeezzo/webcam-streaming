# python 3.11.2

import websockets
import logging
import json
import time
from websockets.sync.server import serve

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst

gi.require_version("GstWebRTC", "1.0")
from gi.repository import GstWebRTC

gi.require_version("GstSdp", "1.0")
from gi.repository import GstSdp

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s (%(filename)s:%(lineno)d) %(message)s",
)


class WebRTCClient:
    def __init__(self, send_to_client):
        self.pipe = None
        self.webrtc = None
        self.appsrc = None
        self.send_to_client = send_to_client
        self.data_channel = None

        self.last_frame_time = time.time()  # Last frame receive time (in seconds)
        self.frame_count = 0  # Counter for frames received in the current second
        self.timestamp = 0

    def reset_pipeline(self):
        """Function to reset the pipeline when the client disconnects."""
        if self.pipe:
            logging.info("resetting the pipeline...")

            # Set pipeline state to NULL to stop it
            self.pipe.set_state(Gst.State.NULL)

            # Remove elements from the pipeline
            self.pipe.remove(self.webrtc)
            if self.appsrc:
                self.pipe.remove(self.appsrc)

            # Clean up data channel connections and any other resources
            if self.data_channel:
                self.data_channel.close()
                self.data_channel = None

            # Clear out other elements if necessary
            self.appsrc = None
            self.webrtc = None
            self.pipe = None
            logging.info("pipeline reset successfully.")

    def prepare_data_channel(self, _, channel, is_local):
        logging.info(f"preparing data channel... {'local' if is_local else 'remote'}")
        self.data_channel = channel
        self.data_channel.connect("on-message-data", self.on_data_channel_data)
        self.data_channel.connect("on-open", self.on_data_channel_open)
        self.data_channel.connect("on-close", self.on_data_channel_close)
        self.data_channel.connect("notify::ready-state", self.on_data_channel_state)

    def on_data_channel_state(self, data_channel, _):
        state = data_channel.get_property("ready-state")
        logging.info(f"data channel state changed -> {state.value_nick}")

    def on_data_channel(self, _, channel):
        logging.info("data channel created")
        self.data_channel = channel

    def on_data_channel_open(self, _):
        logging.info("data channel opened")

    def on_data_channel_close(self, _):
        logging.info("data channel closed")
        self.reset_pipeline()

    def on_data_channel_data(self, _, data):
        # logging.info(f"data channel received -> {data.get_size()} bytes")

        if self.appsrc:
            buf = Gst.Buffer.new_wrapped(data.get_data())

            # fps = 30

            # # Set timestamps
            # buf.pts = self.timestamp
            # buf.duration = Gst.util_uint64_scale_int(1, Gst.SECOND, fps)
            # self.timestamp += buf.duration

            self.appsrc.emit("push_buffer", buf)

        # Track frame rate calculation
        current_time = time.time()
        self.frame_count += 1

        # If more than 1 second has passed, log FPS
        if current_time - self.last_frame_time >= 1:
            actual_fps = self.frame_count
            logging.info(f"Actual Frame Rate: {actual_fps} FPS")
            self.last_frame_time = current_time  # Reset the last frame time
            self.frame_count = 0  # Reset frame count for the next second

    def on_bus_message(self, bus, message):
        t = message.type
        if t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            logging.error(f"GStreamer Error: {err.message} | Debug: {debug}")
        elif t == Gst.MessageType.WARNING:
            warn, debug = message.parse_warning()
            logging.warning(f"GStreamer Warning: {warn.message} | Debug: {debug}")
        elif t == Gst.MessageType.EOS:
            logging.info("GStreamer End of Stream (EOS) reached")
        elif t == Gst.MessageType.STATE_CHANGED:
            if message.src == self.pipe:
                old_state, new_state, pending_state = message.parse_state_changed()
                logging.info(
                    f"Pipeline state changed from {old_state.value_nick} to {new_state.value_nick}"
                )
        else:
            logging.debug(f"GStreamer Message: {t.value_nick}")

    def on_negotiation_needed(self, _):
        logging.info("on_negotiation_needed()")

    def on_ice_candidate(self, _, mlineindex, candidate):
        logging.info("sending ice candidate...")
        icemsg = json.dumps(
            {"ice": {"candidate": candidate, "sdpMLineIndex": mlineindex}}
        )
        self.send_to_client(icemsg)

    def on_ice_gathering_state_notify(self, webrtc, _):
        state = webrtc.get_property("ice-gathering-state")
        logging.info(f"ICE gathering state changed to {state.value_nick}")

    def on_answer_created(self, promise, _, __):
        logging.info("sending answer back to client...")
        assert promise.wait() == Gst.PromiseResult.REPLIED
        reply = promise.get_reply()
        answer = reply.get_value("answer")  # python 3.11
        # answer = reply["answer"]
        promise = Gst.Promise.new()
        self.webrtc.emit("set-local-description", answer, promise)
        promise.interrupt()  # we don't care about the result, discard it
        text = answer.sdp.as_text()
        msg = json.dumps({"sdp": {"type": "answer", "sdp": text}})
        self.send_to_client(msg)

    def on_offer_set(self, promise, _, __):
        assert promise.wait() == Gst.PromiseResult.REPLIED
        promise = Gst.Promise.new_with_change_func(self.on_answer_created, None, None)
        self.webrtc.emit("create-answer", None, promise)

    def set_remote_description(self, sdp):
        logging.info("setting remote description...")
        res, sdpmsg = GstSdp.SDPMessage.new_from_text(sdp)
        offer = GstWebRTC.WebRTCSessionDescription.new(
            GstWebRTC.WebRTCSDPType.OFFER, sdpmsg
        )
        promise = Gst.Promise.new_with_change_func(self.on_offer_set, None, None)
        self.webrtc.emit("set-remote-description", offer, promise)

    def set_ice_candidate(self, ice):
        logging.info("setting ice candidate...")
        candidate = ice["candidate"]
        sdpmlineindex = ice["sdpMLineIndex"]
        self.webrtc.emit("add-ice-candidate", sdpmlineindex, candidate)

    def on_incoming_decodebin_stream(self, _, pad):
        if not pad.has_current_caps():
            logging.warning("pad has no caps, skipping...")
            return

        # handle pad differently - audio/video
        caps = pad.get_current_caps()
        media_type = caps.get_structure(0).get_name()
        if media_type.startswith("video"):
            self.handle_video_stream(pad)
        elif media_type.startswith("audio"):
            self.handle_audio_stream(pad)
        else:
            logging.warning(f"unsupported media type: {media_type}")
            return

    def on_incoming_stream(self, _, pad):
        logging.info("on_incoming_stream()")

        if pad.direction != Gst.PadDirection.SRC:
            return

        decodebin = Gst.ElementFactory.make("decodebin")
        if not decodebin:
            logging.error("decodebin creation failed")
            return

        decodebin.connect("pad-added", self.on_incoming_decodebin_stream)
        self.pipe.add(decodebin)
        decodebin.sync_state_with_parent()
        pad.link(decodebin.get_static_pad("sink"))

    def handle_video_stream(self, pad):
        """Shouldn't be any requests here. MJPEG streamed over raw data channel"""

        logging.warning("received stream on generic webrtc input")

    def handle_audio_stream(self, pad):
        """Handle audio stream. Outputs direclty to an alsasink that is a UAC gadget"""

        logging.info("audio stream received")
        queue = Gst.ElementFactory.make("queue")
        convert = Gst.ElementFactory.make("audioconvert")
        resample = Gst.ElementFactory.make("audioresample")
        rate = Gst.ElementFactory.make("audiorate")
        sink = Gst.ElementFactory.make("autoaudiosink")

        if not convert or not queue or not resample or not rate or not sink:
            logging.error("failed to create audio elements")
            return

        sink.set_property("sync", False)

        self.pipe.add(queue)
        self.pipe.add(convert)
        self.pipe.add(resample)
        self.pipe.add(rate)
        self.pipe.add(sink)
        self.pipe.sync_children_states()

        pad.link(queue.get_static_pad("sink"))
        queue.link(convert)
        convert.link(resample)
        convert.link(resample)
        resample.link(rate)
        rate.link(sink)

        logging.info("audio pipeline linked successfully")

        # Gst.debug_bin_to_dot_file(self.pipe, Gst.DebugGraphDetails.ALL, "pipeline")

    def start_pipeline(self):
        logging.info("creating pipeline...")

        self.pipe = Gst.Pipeline.new("webrtc-pipeline")
        self.webrtc = Gst.ElementFactory.make("webrtcbin", "receive")

        if not self.pipe or not self.webrtc:
            logging.error("failed to create webrtc pipeline")
            return

        # appsrc to receive mjpeg stream from raw data channel
        self.appsrc = Gst.ElementFactory.make("appsrc", "mjpeg_src")
        self.appsrc.set_property("format", Gst.Format.TIME)
        self.appsrc.set_property("block", True)
        self.appsrc.set_property("do-timestamp", True)

        # mjpeg handling pipeline
        dec = Gst.ElementFactory.make("jpegdec")
        conv = Gst.ElementFactory.make("videoconvert")
        sink = Gst.ElementFactory.make("autovideosink")

        if not self.appsrc or not dec or not conv or not sink:
            logging.error("failed to create mjpeg handling pipeline")

        self.pipe.add(self.webrtc)
        self.pipe.add(self.appsrc)
        self.pipe.add(dec)
        self.pipe.add(conv)
        self.pipe.add(sink)

        self.appsrc.link(dec)
        dec.link(conv)
        conv.link(sink)

        self.webrtc.connect("on-negotiation-needed", self.on_negotiation_needed)
        self.webrtc.connect("on-ice-candidate", self.on_ice_candidate)
        self.webrtc.connect(
            "notify::ice-gathering-state", self.on_ice_gathering_state_notify
        )
        self.webrtc.connect("pad-added", self.on_incoming_stream)
        self.webrtc.connect("on-data-channel", self.on_data_channel)
        self.webrtc.connect("prepare-data-channel", self.prepare_data_channel)

        # Attach bus logging
        bus = self.pipe.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self.on_bus_message)

        self.pipe.set_state(Gst.State.PLAYING)
        logging.info("pipeline started successfully!")


def signaling(websocket: websockets.server.ServerConnection):
    logging.info("client connected")

    webrtc = WebRTCClient(websocket.send)
    webrtc.start_pipeline()

    for data in websocket:
        logging.info(f"client -> {data}")
        msg = json.loads(data)

        if "sdp" in msg:
            sdp = msg["sdp"]["sdp"]
            webrtc.set_remote_description(sdp)

        elif "ice" in msg:
            ice = msg["ice"]
            webrtc.set_ice_candidate(ice)
            pass


def main():
    Gst.init(None)

    with serve(signaling, "localhost", 3000) as server:
        logging.info("server started on http://localhost:3000")
        server.serve_forever()


if __name__ == "__main__":
    main()
