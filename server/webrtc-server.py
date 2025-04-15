import json
import asyncio
import logging
import gi
import time

from websockets.asyncio.server import serve 
from websockets.asyncio.server import ServerConnection
from aiortc import RTCPeerConnection, RTCIceCandidate, RTCSessionDescription, MediaStreamTrack, MediaStreamError
from aiortc.contrib.media import MediaRelay, MediaRecorder

gi.require_version("Gst", "1.0")
from gi.repository import Gst

gi.require_version("GstWebRTC", "1.0")
from gi.repository import GstWebRTC

gi.require_version("GstSdp", "1.0")
from gi.repository import GstSdp


LOGGIN_FORMAT = "%(levelname)s (%(filename)s:%(lineno)d) %(message)s"


# media_pipeline: Gst.Element = None
# webrtc_handler: Gst.Bin = None

pc: RTCPeerConnection = None
loop: asyncio.AbstractEventLoop = None
tracks: list[MediaStreamTrack] = []
# relay: MediaRelay = None

# def on_ice_candidate(*_) -> None:
#     logging.info("Call: on_ice_candidate()")


# def on_incoming_stream(*_) -> None:
#     logging.info("Call: on_incoming_stream()")


# def on_data_channel(*_) -> None:
#     logging.info("Call: on_data_channel()")


# def prepare_data_channel(*_) -> None:
#     logging.info("Call: prepare_data_channel()")


# def on_connection_state_notify(*_) -> None:
#     logging.info("Call: on_connection_state_notify()")


# def on_ice_gathering_state_notify(*_) -> None:
#     logging.info("Call: on_ice_gathering_state_notify()")


# # def on_test(promise: Gst.Promise, *_) -> None:
# #     assert promise.wait() == Gst.PromiseResult.REPLIED
# #     offer = promise.get_reply()
# #     logging.info(f"SDP Offer(): {offer.to_string()}")


# # async def create_sdp_offer(websocket: ServerConnection) -> None:
# #     await websocket.send(json.dumps("Hello"))


# def init_webrtc_pipeline() -> tuple[Gst.Element, Gst.Bin]:
#     logging.info("Call -> init_webrtc_pipeline()")
#     Gst.init(None)

#     pipeline = Gst.Pipeline.new("webrtc-pipeline")
#     webrtc = Gst.ElementFactory.make("webrtcbin", "receive")

#     if not (pipeline and webrtc):
#         raise RuntimeError("Webrtc pipeline initialization failed")

#     webrtc.set_property("latency", 50)
#     # webrtc.connect("on-negotiation-needed", lambda _ : asyncio.run_coroutine_threadsafe(create_sdp_offer(websocket), event_loop))
#     webrtc.connect("on-ice-candidate", on_ice_candidate)
#     webrtc.connect("pad-added", on_incoming_stream)
#     webrtc.connect("on-data-channel", on_data_channel)
#     webrtc.connect("prepare-data-channel", prepare_data_channel)
#     webrtc.connect("notify::connection-state", on_connection_state_notify)
#     webrtc.connect("notify::signaling-state", lambda webrtc, _ : logging.info(f"Signaling state -> { webrtc.get_property('signaling-state').value_nick }"))
#     webrtc.connect("notify::ice-gathering-state", on_ice_gathering_state_notify)

#     pipeline.add(webrtc)
#     pipeline.set_state(Gst.State.PLAYING)

#     logging.info("WebRTC pipeline has started!")

#     return pipeline, webrtc


# def create_sdp_answer(websocket: ServerConnection, json_sdp: any) -> None:
#     logging.info(f"Sending SDP Answer: {json.dumps(json_sdp)}")


# def accept_sdp_offer(websocket: ServerConnection, json_sdp: any) -> None:
#     def on_create_answer(promise: Gst.Promise, *_) -> None:
#         logging.info("Call -> on_create_answer()")
#         assert promise.wait() == Gst.PromiseResult.REPLIED
#         answer = promise.get_reply().get_value("answer")
#         logging.info(f"Answer: {answer.sdp.as_text()}")
#         # promise = Gst.Promise.new()
#         # webrtc_handler.emit("set-local-description", answer, promise)
#         # promise.interrupt()

#         # create_sdp_answer(None, {"sdp": {"type": "answer", "sdp": answer.sdp.as_text()}})


#     def on_remote_desc_changed(promise: Gst.Promise, *_) -> None:
#         logging.info("Call -> on_remote_desc_changed()")
#         assert promise.wait() == Gst.PromiseResult.REPLIED
#         promise = Gst.Promise.new_with_change_func(on_create_answer, None, None)
#         webrtc_handler.emit("create-answer", None, promise)


#     logging.info("Call -> accept_sdp_offer()")
#     _, msg = GstSdp.sdp_message_new_from_text(json_sdp["sdp"]["sdp"])
#     offer = GstWebRTC.WebRTCSessionDescription.new(GstWebRTC.WebRTCSDPType.OFFER, msg)
#     promise = Gst.Promise.new_with_change_func(on_remote_desc_changed, None, None)
#     webrtc_handler.emit("set-remote-description", offer, promise)


# def accept_ice_candidate(json_ice: any) -> None:
#     logging.info("Call -> accept_ice_candidate()")
#     webrtc_handler.emit("add-ice-candidate", json_ice["ice"]["sdpMLineIndex"], json_ice["ice"]["candidate"])


def init_gstreamer_pipeline(device: str = "/dev/video0") -> Gst.Element:
    Gst.init(None)

    pipeline = Gst.Pipeline.new("webcam-pipeline")
    appsrc = Gst.ElementFactory.make("videotestsrc")
    jpegenc = Gst.ElementFactory.make("jpegenc")
    videorate = Gst.ElementFactory.make("videorate")
    videosink = Gst.ElementFactory.make("uvcsink")

    videosink.get_child_by_name("v4l2sink").set_property("device", device)

    pipeline.add(appsrc)
    pipeline.add(jpegenc)
    pipeline.add(videorate)
    pipeline.add(videosink)

    appsrc.link(jpegenc)
    jpegenc.link(videorate)
    videorate.link(videosink)

    return pipeline


def release_gstreamer_pipeline(pipeline: Gst.Element) -> None:
    pipeline.set_state(Gst.State.PAUSED)


async def run_gstreamer_stream(pipeline: Gst.Element, track: MediaStreamTrack) -> None:
    while True:
        try:
            frame = await track.recv()
        except MediaStreamError:
            return
        
        logging.info(frame)


def start_gstreamer_pipeline(pipeline: Gst.Element) -> None:
    pipeline.set_state(Gst.State.PLAYING)
    for track in tracks:
        asyncio.ensure_future(run_gstreamer_stream(pipeline, track))


def init_incomming_track(track: MediaStreamTrack) -> None:
    pc.addTrack(track)
    tracks.append(track)


def start_recording(connectionState: str) -> None:
    if connectionState == "connected":
        pipeline = init_gstreamer_pipeline("/dev/video1")
        start_gstreamer_pipeline(pipeline)
    elif connectionState == "closed":
        release_gstreamer_pipeline(pipeline)


async def create_sdp_answer(websocket: ServerConnection, sdp: RTCSessionDescription) -> None:
    logging.info(f"Creating SDP Answer: {json.dumps({ 'sdp': sdp.sdp, 'type': sdp.type })}")
    await pc.setLocalDescription(sdp)
    await websocket.send(json.dumps({ 'sdp': pc.localDescription.sdp, 'type': pc.localDescription.type }))


async def accept_sdp_offer(websocket: ServerConnection, sdp: RTCSessionDescription) -> None:
    await pc.setRemoteDescription(sdp)
    await create_sdp_answer(websocket, await pc.createAnswer())


async def accept_ice_candidate(ice: RTCIceCandidate) -> None:
    await pc.addIceCandidate(ice)


async def echo(websocket: ServerConnection) -> None:
    async for message in websocket:
        json_msg = json.loads(message)
        if "sdp" in json_msg:
            logging.info(f"Accepting SDP Offer: {json.dumps(json_msg)}")
            asyncio.create_task(accept_sdp_offer(websocket, RTCSessionDescription(sdp=json_msg["sdp"], type=json_msg["type"])))
        elif "ice" in json_msg:
            logging.info(f"Accepting ICE Condidate: {json.dumps(json_msg)}")
            asyncio.create_task(accept_ice_candidate(RTCIceCandidate(
                component       = json_msg["ice"]["component"],
                foundation      = json_msg["ice"]["foundation"],
                ip              = json_msg["ice"]["ip"],
                port            = json_msg["ice"]["port"],
                priority        = json_msg["ice"]["priority"],
                protocol        = json_msg["ice"]["protocol"],
                type            = json_msg["ice"]["type"],
                relatedAddress  = json_msg["ice"]["relatedAddress"],
                relatedPort     = json_msg["ice"]["relatedPort"],
                sdpMid          = json_msg["ice"]["sdpMid"],
                sdpMLineIndex   = json_msg["ice"]["sdpMLineIndex"],
                tcpType         = json_msg["ice"]["tcpType"]
            )))


async def main() -> None:
    global pc, recoder, relay
    
    pc = RTCPeerConnection()
    relay = MediaRelay()

    @pc.on("track")
    def on_tack(track: MediaStreamTrack):
        init_incomming_track(relay.subscribe(track))

    @pc.on("connectionstatechange")
    def on_connection_state_change():
        start_recording(pc.connectionState)

    # global media_pipeline, webrtc_handler
    # media_pipeline, webrtc_handler = init_webrtc_pipeline()

    async with serve(echo, "0.0.0.0", 3000) as server:
        await server.serve_forever()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format=LOGGIN_FORMAT)

    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
