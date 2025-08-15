# python 3.11.2

import websockets
import websockets.server
import logging
import json
import time
import asyncio
import ssl
import os
import glob
import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst

gi.require_version("GstWebRTC", "1.0")
from gi.repository import GstWebRTC

gi.require_version("GstSdp", "1.0")
from gi.repository import GstSdp

gi.require_version("GstApp", "1.0")
from gi.repository import GstApp


class WebRTCPipeline:
    def __init__(self):
        self.pipe = None
        self.appsrc = None
        self.data_channel = None

        # Single signaling connection-related fields
        self.event_loop = None
        self.send_to_client = None
        self.webrtc = None
        self.audio_stream = None # list of elements in audio chain
        self.decodebins = {} # { pad-name: decodebin }
        self.last_frame_time = 0
        self.frame_count = 0

        self.bus = None
        self.bus_watch_id = None
        
    def __del__(self):
        logging.info("cleaning up")

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
                self.bus.timed_pop_filtered(500 * Gst.MILLISECOND, Gst.MessageType.EOS)
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

        self.audio_stream = None
        self.is_active = False
        logging.info("pipeline destroyed")

    def on_client_connected(self, send_to_client, loop) -> bool:
        logging.info("on_client_connected()")

        if self.event_loop:
            logging.error("on_client_connected() called while the pipeline is already in use by a client")
            return False

        self.webrtc = Gst.ElementFactory.make("webrtcbin", "receive")
        if not self.webrtc:
            logging.error("unable to create webrtcbin element")
            return False
        
        self.event_loop = loop
        self.send_to_client = send_to_client
        self.last_frame_time = 0
        self.frame_count = 0

        # jitterbuffer latency
        self.webrtc.set_property("latency", 0)

        self.webrtc.connect("pad-added", self.on_incoming_stream)
        self.webrtc.connect("pad-removed", self.on_stream_disconnect)
        self.webrtc.connect("on-negotiation-needed", self.on_negotiation_needed)
        self.webrtc.connect("on-ice-candidate", self.on_ice_candidate)
        self.webrtc.connect("on-data-channel", self.on_data_channel)
        self.webrtc.connect("prepare-data-channel", self.prepare_data_channel)
        self.webrtc.connect("notify::connection-state", self.on_connection_state_notify)
        self.webrtc.connect("notify::signaling-state", self.on_signaling_state_notify)
        self.webrtc.connect(
            "notify::ice-gathering-state", self.on_ice_gathering_state_notify
        )

        self.pipe.add(self.webrtc)
        self.webrtc.sync_state_with_parent()

        return True

    def on_client_disconnected(self):
        logging.info("on_client_disconnected()")
        self.event_loop = None
        self.send_to_client = None

        self.clear_audio_stream()

        # on_stream_disconnected won't be called since we're destroying the webrtbin
        # so destroy them manually
        for name, decodebin in list(self.decodebins.items()):
            try:
                decodebin.set_state(Gst.State.NULL)
            except Exception as e: logging.error(f"error NULLifying decodebin: {e}")
            try:
                self.pipe.remove(decodebin)
            except Exception: pass
        self.decodebins.clear()

        if self.webrtc:
            try:
                self.webrtc.set_state(Gst.State.NULL)
            except Exception as e: logging.error(f"error NULLifying webrtcbin: {e}")
            try:
                self.pipe.remove(self.webrtc)
            except Exception: pass
            self.webrtc = None

        # Disconnect peer data channel. We assume that if WS is disconnected, the client shouldn't be interacted with anymore
        if self.data_channel:
            try: self.data_channel.close()
            except Exception: pass
            self.data_channel = None

        logging.info("disconnect cleanup done")

    def send_client(self, msg):
        if not self.send_to_client:
            return
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
        self.data_channel = None

    def on_data_channel_data(self, _, data):
        if self.appsrc:
            buf = Gst.Buffer.new_wrapped(data.get_data())
            # print("len -> ", len(data.get_data()))
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
        self.send_client(icemsg)

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
        logging.info(f"Got reply: {reply.to_string()}")
        answer = reply.get_value("answer")
        promise = Gst.Promise.new()
        self.webrtc.emit("set-local-description", answer, promise)
        promise.interrupt()  # we don't care about the result, discard it
        text = answer.sdp.as_text()
        msg = json.dumps({"sdp": {"type": "answer", "sdp": text}})
        self.send_client(msg)

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
            logging.error("received stream on generic webrtc input")
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

        if pad.link(decodebin.get_static_pad("sink")) != Gst.PadLinkReturn.OK:
            logging.error("failed to link incoming pad to decodebin")
            self.pipe.remove(decodebin)
            return
        
        self.decodebins[pad.get_name()] = decodebin

    def on_stream_disconnect(self, _, pad):
        logging.info("on_stream_disconnect()")

        self.clear_audio_stream()

        # Remove the decodebin that was fed by this pad
        key = pad.get_name()
        decodebin = self.decodebins.pop(key, None)
        if decodebin:
            try:
                decodebin.set_state(Gst.State.NULL)
            except Exception:
                pass
            try:
                # Unlink its sink pad’s peer (incoming pad) just in case
                sinkpad = decodebin.get_static_pad("sink")
                if sinkpad:
                    peer = sinkpad.get_peer()
                    if peer:
                        peer.unlink(sinkpad)
            except Exception:
                pass
            try:
                self.pipe.remove(decodebin)
            except Exception:
                pass

    def handle_audio_stream(self, pad):
        """Handle audio stream. Outputs direclty to an alsasink that is a UAC gadget"""

        logging.info("audio stream received")
        if self.audio_stream:
            self.clear_audio_stream()

        queue = Gst.ElementFactory.make("queue")
        convert = Gst.ElementFactory.make("audioconvert")
        resample = Gst.ElementFactory.make("audioresample")
        sink = Gst.ElementFactory.make("alsasink")

        if not queue or not convert or not resample or not sink:
            logging.error("failed to create audio elements")
            return

        self.audio_stream = [queue, convert, resample, sink]

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

        self.audio_upstream_pad = pad
        self.audio_queue_sink = queue.get_static_pad("sink")

        pad.link(self.audio_queue_sink)
        queue.link(convert)
        convert.link(resample)
        resample.link(sink)

        for elem in self.audio_stream:
            elem.sync_state_with_parent()

        logging.info("audio pipeline linked successfully")

    def clear_audio_stream(self):
        if not self.audio_stream:
            return
        
        logging.info("cleaning up audio stream elements...")

        # Unlink upstream peer (incoming pad → queue.sink)
        try:
            if getattr(self, "audio_upstream_pad", None) and getattr(self, "audio_queue_sink", None):
                try:
                    self.audio_upstream_pad.unlink(self.audio_queue_sink)
                except Exception:
                    # some GI builds require unlink from the peer pad direction:
                    peer = self.audio_queue_sink.get_peer()
                    if peer:
                        peer.unlink(self.audio_queue_sink)
        except Exception: pass
        self.audio_upstream_pad = None
        self.audio_queue_sink = None

        for elem in self.audio_stream:
            try:
                elem.set_state(Gst.State.NULL)
            except Exception: pass

        # Unlink in reverse order (sink → src)
        try:
            self.audio_stream[-1].unlink(self.audio_stream[-2])
            self.audio_stream[-2].unlink(self.audio_stream[-3])
            self.audio_stream[-3].unlink(self.audio_stream[-4])
        except Exception as e:
            logging.warning(f"unlink failed: {e}")

        # Remove from pipeline
        for elem in self.audio_stream:
            try:
                self.pipe.remove(elem)
            except Exception as e:
                logging.warning(f"remove failed: {e}")

        self.audio_stream = None

    def init_pipeline(self, uvc_gadget_device):
        logging.info("creating pipeline...")

        if self.pipe:
            logging.info("existing pipeline detected, nothing to do")
            return

        self.pipe = Gst.Pipeline.new("webrtc-pipeline")
        
        if not self.pipe:
            logging.error("failed to create webrtc-pipeline")
            return

        # appsrc to receive mjpeg stream from raw data channel
        self.appsrc = Gst.ElementFactory.make("appsrc", "mjpeg_src")
        self.appsrc.set_property("do-timestamp", True)
        self.appsrc.set_property("format", Gst.Format.TIME)
        self.appsrc.set_property("is-live", True)
        self.appsrc.set_property("max-buffers", 5)
        self.appsrc.set_property("leaky-type", GstApp.AppLeakyType.DOWNSTREAM)

        # mjpeg handling pipeline
        queue = Gst.ElementFactory.make("queue")
        parse = Gst.ElementFactory.make("jpegparse")
        rate = Gst.ElementFactory.make("videorate")
        sink = Gst.ElementFactory.make("uvcsink")

        rate.set_property("drop-only", True)
        rate.set_property("skip-to-first", True)
        self.rate = rate

        queue.set_property("notify-levels", True)
        queue.set_property("leaky", 2)
        queue.set_property("max-size-buffers", 50)

        if not self.appsrc or not sink:
            logging.error("failed to create mjpeg handling pipeline")
            return

        v4l2sink = sink.get_child_by_name("v4l2sink")
        v4l2sink.set_property("device", uvc_gadget_device)
        v4l2sink.set_property("sync", False)
        v4l2sink.set_property("async", False)
        v4l2sink.set_property("max_lateness", 0)
        v4l2sink.set_property("processing_deadline", 0)

        self.v4l2sink = v4l2sink

        self.pipe.add(self.appsrc)
        self.pipe.add(queue)
        self.pipe.add(parse)
        self.pipe.add(rate)
        self.pipe.add(sink)

        self.appsrc.link(queue)
        queue.link(parse)
        parse.link(rate)
        rate.link(sink)

        # Attach bus logging
        self.bus = self.pipe.get_bus()
        if self.bus:
            self.bus.add_signal_watch()
            self.bus_watch_id = self.bus.connect("message", self.on_bus_message)

        self.pipe.set_state(Gst.State.PLAYING)

        logging.info("pipeline started successfully!")

def get_uvc_gadget_device(usb_path="fe980000.usb"):
    for device_path in glob.glob('/sys/class/video4linux/video*'):
        # Get the real path to resolve symbolic links
        real_path = os.path.realpath(device_path)
        if usb_path in real_path:
            return "/dev/" + os.path.basename(device_path) # e.g. /dev/video0
    raise RuntimeError("UVC gadget video device not found.")

async def signaling(websocket: websockets.server.ServerConnection, webrtc: WebRTCPipeline):
    logging.info("[signaling]: client connected")
    loop = asyncio.get_running_loop()
    if not webrtc.on_client_connected(websocket.send, loop):
        logging.error("[signaling]: disconnecting client")
        websocket.close()
        return

    async def handle_disconnect(websocket: websockets.server.ServerConnection):
        """Callback function to handle WebSocket disconnection."""
        await websocket.wait_closed()
        logging.info("[signaling]: handle_disconnect")
        webrtc.on_client_disconnected()
    disconnect_task = asyncio.create_task(handle_disconnect(websocket))

    try:
        async for data in websocket:
            logging.info(f"[signaling]: client -> {data}")
            msg = json.loads(data)

            if "sdp" in msg:
                sdp = msg["sdp"]["sdp"]
                webrtc.set_remote_description(sdp)

            elif "ice" in msg:
                ice = msg["ice"]
                webrtc.set_ice_candidate(ice)
                pass

    except websockets.exceptions.ConnectionClosed:
        logging.info("[signaling]: client connection closed unexpectedly")
    finally:
        # ensure disconnect task cancelled and pipeline fully torn down
        if not disconnect_task.done():
            disconnect_task.cancel()
        try:
            webrtc.on_client_disconnected()
        except Exception:
            pass
        logging.info("[signaling]: finished")


async def main():
    Gst.init(None)

    uvc_gadget_device = get_uvc_gadget_device()
    webrtc = WebRTCPipeline()
    webrtc.init_pipeline(uvc_gadget_device)

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

    async with websockets.serve(
            lambda websocket : signaling(websocket, webrtc),
            "0.0.0.0",
            3000,
            ssl=ssl_context
        ):
        await asyncio.Future()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s (%(filename)s:%(lineno)d) %(message)s",
    )
    asyncio.run(main())
