# python 3.11.2

import websockets
import websockets.server
import logging
import json
import time
import asyncio
import ssl

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst

gi.require_version("GstWebRTC", "1.0")
from gi.repository import GstWebRTC

gi.require_version("GstSdp", "1.0")
from gi.repository import GstSdp

gi.require_version("GstApp", "1.0")
from gi.repository import GstApp


class WebRTCClient:
    def __init__(self, send_to_client, loop):
        self.pipe = None
        self.webrtc = None
        self.appsrc = None
        self.data_channel = None

        self.event_loop = loop
        self.send_to_client = send_to_client

        self.last_frame_time = time.time()
        self.frame_count = 0
        self.timestamp = 0

        self.bus = None
        self.bus_watch_id = None
        self.signal_ids = []     # store webrtc.connect() ids
        self.data_channel_ids = []  # store data channel signal ids if needed

    def stop_pipeline(self, wait_eos_ms=500):
        """Properly stop and teardown the GStreamer pipeline and disconnect signals."""
        logging.info("Stopping pipeline (stop_pipeline)")

        # Try to end stream cleanly
        try:
            if self.appsrc:
                try:
                    self.appsrc.emit("end-of-stream")
                except Exception:
                    # some appsrc states may not accept eos; ignore
                    pass
        except Exception:
            pass

        # Wait shortly for EOS to propagate on the bus
        if self.bus:
            try:
                # drain EOS messages up to wait_eos_ms
                self.bus.timed_pop_filtered(wait_eos_ms * Gst.MILLISECOND, Gst.MessageType.EOS)
            except Exception:
                pass

        # Disconnect bus handler and remove signal watch
        if self.bus and self.bus_watch_id:
            try:
                self.bus.disconnect(self.bus_watch_id)
            except Exception:
                pass
            try:
                self.bus.remove_signal_watch()
            except Exception:
                pass
            self.bus = None
            self.bus_watch_id = None

        # Disconnect all stored webrtc signal handlers
        if self.webrtc:
            for sid in self.signal_ids:
                try:
                    self.webrtc.disconnect(sid)
                except Exception:
                    pass
            self.signal_ids = []

        # If webrtc element exists, set it to NULL state first
        try:
            if self.webrtc:
                self.webrtc.set_state(Gst.State.NULL)
        except Exception:
            pass

        # Set pipeline to NULL and remove elements
        if self.pipe:
            try:
                self.pipe.set_state(Gst.State.NULL)
            except Exception:
                pass

            # Remove all children from pipeline (safe even if already NULL)
            try:
                elems = self.pipe.iterate_elements()
                # iterate_elements returns a Gst.Iterator — easier to just clear references by name:
                for elem in list(self.pipe.children):
                    try:
                        self.pipe.remove(elem)
                    except Exception:
                        pass
            except Exception:
                # fallback if iterate_elements isn't available / different bindings
                pass

            # clear Python refs so GObject can unref
            self.pipe = None

        # Clear element refs
        self.webrtc = None
        self.appsrc = None
        self.data_channel = None
        self.v4l2sink = None
        self.rate = None

        self.is_active = False
        logging.info("Pipeline stopped and torn down")

    def cleanup(self):
        logging.info("cleanup() -> delegating to stop_pipeline()")
        self.stop_pipeline()

    # def cleanup(self):
    #     """Clean up resources when client disconnects"""
    #     logging.info("Cleaning up WebRTC client resources")

    #     if self.pipe:
    #         # EOS to the pipeline
    #         self.pipe.send_event(Gst.Event.new_eos())

    #         # Wait for EOS to propagate
    #         bus = self.pipe.get_bus()
    #         if bus:
    #             bus.timed_pop_filtered(Gst.CLOCK_TIME_NONE, Gst.MessageType.EOS)

    #         self.pipe.set_state(Gst.State.NULL)
    #         self.pipe = None

    #     self.webrtc = None
    #     self.appsrc = None
    #     self.data_channel = None
    #     self.is_active = False

    def send_soon(self, msg):
        asyncio.run_coroutine_threadsafe(self.send_to_client(msg), self.event_loop)

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

    def on_data_channel_data(self, _, data):

        if self.appsrc:
            buf = Gst.Buffer.new_wrapped(data.get_data())

            # print("len -> ", len(data.get_data()))

            # Use wall clock time for both audio and video synchronization
            # timestamp = int(time.time() * Gst.SECOND)  # Current time in nanoseconds
            # buf.pts = timestamp
            # buf.duration = int((1 / 30) * Gst.SECOND)  # Default to 30fps

            # Calculate duration based on actual frame rate
            # if self.frame_count > 0:  # After first frame
            #     now = time.time()
            #     elapsed = now - self.last_frame_time
            #     avg_frame_duration = elapsed / self.frame_count
            #     buf.duration = int(avg_frame_duration * Gst.SECOND)
            # else:

            # clock = self.pipe.get_clock()
            # if clock:
            #     running_time = clock.get_time() - self.pipe.get_base_time()
            #     buf.pts = running_time
            #     buf.duration = Gst.CLOCK_TIME_NONE

            # timestamp = Gst.util_uint64_scale(int(time.time() * Gst.SECOND), 1, 1)
            # buf.pts = timestamp
            # buf.duration = Gst.util_uint64_scale(1, Gst.SECOND, 30)

            self.appsrc.emit("push_buffer", buf)

            # value_appsrc = self.appsrc.get_property("current-level-buffers")
            # logging.info(f"APPSRC BUFFERS -> {value_appsrc}")

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
        self.send_soon(icemsg)

    def on_queue_current_level_buffers(self, queue, _):
        value = queue.get_property("current-level-buffers")
        if value > 10:
            logging.info(f"QUEUE BUFFERS -> {value}")

    def on_ice_gathering_state_notify(self, webrtc, _):
        state = webrtc.get_property("ice-gathering-state")
        logging.info(f"ICE GATHERING STATE -> {state.value_nick}")

    def on_connection_state_notify(self, webrtc, _):
        state = webrtc.get_property("connection-state")
        logging.info(f"CONNECTION STATE -> {state.value_nick}")

    def on_signaling_state_notify(self, webrtc, _):
        state = webrtc.get_property("signaling-state")
        logging.info(f"SIGNALING STATE -> {state.value_nick}")

    def on_answer_created(self, promise, _, __):
        logging.info("sending answer back to client...")
        assert promise.wait() == Gst.PromiseResult.REPLIED
        reply = promise.get_reply()
        print(reply.to_string())
        answer = reply.get_value("answer")
        promise = Gst.Promise.new()
        self.webrtc.emit("set-local-description", answer, promise)
        promise.interrupt()  # we don't care about the result, discard it
        text = answer.sdp.as_text()
        msg = json.dumps({"sdp": {"type": "answer", "sdp": text}})
        self.send_soon(msg)

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

        # handles audio/video pads differently
        caps = pad.get_current_caps()
        media_type = caps.get_structure(0).get_name()
        if media_type.startswith("video"):
            self.handle_video_stream(pad)
        elif media_type.startswith("audio"):
            self.handle_audio_stream(pad)
        else:
            logging.warning(f"unsupported media type: {media_type}")
            return

    def on_stream_disconnect(self, _, pad):
        logging.info("on_stream_disconnect()")

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
        sink = Gst.ElementFactory.make("alsasink")

        if not queue or not convert or not resample or not sink:
            logging.error("failed to create audio elements")
            return

        sink.set_property("device", "hw:UAC2Gadget")
        sink.set_property("sync", False)  # Crucial for low latency
        sink.set_property("async", False)
        sink.set_property("buffer-time", 20000)  # 20ms buffer (microseconds)
        sink.set_property("latency-time", 20000)  # 20ms latency

        queue.set_property("notify-levels", True)
        queue.set_property("leaky", 2)
        queue.set_property("max-size-buffers", 10)
        queue.connect(
            "notify::current-level-buffers", self.on_queue_current_level_buffers
        )

        self.pipe.add(queue)
        self.pipe.add(convert)
        self.pipe.add(resample)
        self.pipe.add(sink)
        self.pipe.sync_children_states()

        pad.link(queue.get_static_pad("sink"))
        queue.link(convert)
        convert.link(resample)
        resample.link(sink)

        logging.info("audio pipeline linked successfully")

        # debug purpose
        # Gst.debug_bin_to_dot_file(self.pipe, Gst.DebugGraphDetails.ALL, "pipeline")

    def start_pipeline(self):
        logging.info("creating pipeline...")

        if self.pipe:
            logging.info("existing pipeline detected, stopping first")
            self.stop_pipeline()

        self.pipe = Gst.Pipeline.new("webrtc-pipeline")
        self.webrtc = Gst.ElementFactory.make("webrtcbin", "receive")

        if not self.pipe or not self.webrtc:
            logging.error("failed to create webrtc pipeline")
            return

        # appsrc to receive mjpeg stream from raw data channel
        self.appsrc = Gst.ElementFactory.make("appsrc", "mjpeg_src")
        self.appsrc.set_property("do-timestamp", True)
        self.appsrc.set_property("format", Gst.Format.TIME)
        self.appsrc.set_property("is-live", True)
        # self.appsrc.set_property("block", True)
        # self.appsrc.set_property("max-latency", 10)
        # self.appsrc.set_property("min-latency", 0)
        self.appsrc.set_property("max-buffers", 5)
        self.appsrc.set_property("leaky-type", GstApp.AppLeakyType.DOWNSTREAM)

        # mjpeg handling pipeline
        queue = Gst.ElementFactory.make("queue")
        parse = Gst.ElementFactory.make("jpegparse")
        rate = Gst.ElementFactory.make("videorate")
        sink = Gst.ElementFactory.make("uvcsink")

        # rate.set_property("max-rate", 0)
        rate.set_property("drop-only", True)
        rate.set_property("skip-to-first", True)
        self.rate = rate

        queue.set_property("notify-levels", True)
        queue.set_property("leaky", 2)
        queue.set_property("max-size-buffers", 50)
        # queue.connect("notify::current-level-buffers", self.on_queue_current_level_buffers)

        if not self.appsrc or not sink:
            logging.error("failed to create mjpeg handling pipeline")
            return

        # TODO: replace hardcoded device with actual UVC
        v4l2sink = sink.get_child_by_name("v4l2sink")
        v4l2sink.set_property("device", "/dev/video0")
        v4l2sink.set_property("sync", False)
        v4l2sink.set_property("async", False)
        v4l2sink.set_property("max_lateness", 0)
        v4l2sink.set_property("processing_deadline", 0)

        self.v4l2sink = v4l2sink

        self.pipe.add(self.webrtc)
        self.pipe.add(self.appsrc)
        self.pipe.add(queue)
        self.pipe.add(parse)
        self.pipe.add(rate)
        self.pipe.add(sink)

        self.appsrc.link(queue)
        queue.link(parse)
        parse.link(rate)
        rate.link(sink)

        # jitterbuffer latency indeed
        self.webrtc.set_property("latency", 0)

        # Attach bus logging
        self.bus = self.pipe.get_bus()
        if self.bus:
            self.bus.add_signal_watch()
            # store bus connect id so we can disconnect later
            self.bus_watch_id = self.bus.connect("message", self.on_bus_message)

        self.signal_ids.append(self.webrtc.connect("pad-added", self.on_incoming_stream))
        self.signal_ids.append(self.webrtc.connect("pad-removed", self.on_stream_disconnect))

        self.signal_ids.append(self.webrtc.connect("on-negotiation-needed", self.on_negotiation_needed))
        self.signal_ids.append(self.webrtc.connect("on-ice-candidate", self.on_ice_candidate))
        self.signal_ids.append(self.webrtc.connect("on-data-channel", self.on_data_channel))
        self.signal_ids.append(self.webrtc.connect("prepare-data-channel", self.prepare_data_channel))
        self.signal_ids.append(self.webrtc.connect("notify::connection-state", self.on_connection_state_notify))
        self.signal_ids.append(self.webrtc.connect("notify::signaling-state", self.on_signaling_state_notify))
        self.signal_ids.append(self.webrtc.connect(
            "notify::ice-gathering-state", self.on_ice_gathering_state_notify
        ))

        self.pipe.set_state(Gst.State.PLAYING)

        logging.info("pipeline started successfully!")


async def handle_disconnect(websocket: websockets.server.ServerConnection, webrtc):
    """Callback function to handle WebSocket disconnection."""
    await websocket.wait_closed()
    logging.info("handle_disconnect")
    webrtc.cleanup()


async def signaling(websocket: websockets.server.ServerConnection):
    logging.info("client connected")
    loop = asyncio.get_running_loop()
    webrtc = WebRTCClient(websocket.send, loop)
    webrtc.start_pipeline()

    disconnect_task = asyncio.create_task(handle_disconnect(websocket, webrtc))

    try:
        async for data in websocket:
            logging.info(f"client -> {data}")
            msg = json.loads(data)

            if "sdp" in msg:
                sdp = msg["sdp"]["sdp"]
                webrtc.set_remote_description(sdp)

            elif "ice" in msg:
                ice = msg["ice"]
                webrtc.set_ice_candidate(ice)
                pass

    except websockets.exceptions.ConnectionClosed:
        logging.info("client connection closed unexpectedly.")
    finally:
        # ensure disconnect task cancelled and pipeline fully torn down
        if not disconnect_task.done():
            disconnect_task.cancel()
        try:
            webrtc.stop_pipeline()
        except Exception:
            pass
        logging.info("signaling finished, pipeline cleaned up.")


async def main():
    Gst.init(None)

    # SSL Configuration using existing PiKVM certificates
    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_context.load_cert_chain(
        certfile="/etc/kvmd/nginx/ssl/server.crt",
        keyfile="/etc/kvmd/nginx/ssl/server.key",
    )

    # Security hardening (recommended)
    ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
    ssl_context.set_ciphers("ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384")
    ssl_context.options |= (
        ssl.OP_NO_SSLv2 | ssl.OP_NO_SSLv3 | ssl.OP_NO_TLSv1 | ssl.OP_NO_TLSv1_1
    )

    async with websockets.serve(signaling, "0.0.0.0", 3000, ssl=ssl_context):
        await asyncio.Future()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s (%(filename)s:%(lineno)d) %(message)s",
    )
    asyncio.run(main())
