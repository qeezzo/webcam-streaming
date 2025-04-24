import json
import asyncio
import logging
import gi
import ctypes
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


pc: RTCPeerConnection = None
loop: asyncio.AbstractEventLoop = None
tracks: list[MediaStreamTrack] = []


def init_gstreamer_pipeline(device: str = "/dev/video0") -> Gst.Element:
    Gst.init(None)

    pipeline = Gst.Pipeline.new("webcam-pipeline")
    videosrc = Gst.ElementFactory.make("appsrc", "videosrc")
    jpegenc = Gst.ElementFactory.make("jpegenc")
    videorate = Gst.ElementFactory.make("videorate")
    videosink = Gst.ElementFactory.make("uvcsink")

    videosrc.set_property("do-timestamp", True)
    videosink.get_child_by_name("v4l2sink").set_property("device", device)

    pipeline.add(videosrc)
    pipeline.add(jpegenc)
    pipeline.add(videorate)
    pipeline.add(videosink)

    videosrc.link(jpegenc)
    jpegenc.link(videorate)
    videorate.link(videosink)

    return pipeline


def release_gstreamer_pipeline(pipeline: Gst.Element) -> None:
    pipeline.set_state(Gst.State.PAUSED)


async def run_gstreamer_stream(pipeline: Gst.Element, track: MediaStreamTrack, last_time) -> None:
    while True:
        try:
            frame = await track.recv()
        except MediaStreamError:
            return

        logging.info(type(frame))        
        # appsrc = pipeline.get_by_name("videosrc")
        # yuv420_buffer_size = int(frame.width * frame.height * 3 / 2)

        # if frame.width == 800 and frame.height == 600:
        #     memory_view = (ctypes.c_char * yuv420_buffer_size).from_address(frame.planes[0].buffer_ptr)

        #     buffer = Gst.Buffer.new_wrapped(memory_view)
        #     appsrc.emit("push-buffer", buffer)

        #     logging.info(f"Time: {time.time() - last_time} - {memory_view}")
        #     last_time = time.time()


def start_gstreamer_pipeline(pipeline: Gst.Element) -> None:
    pipeline.set_state(Gst.State.PLAYING)
    for track in tracks:
        curr_time = time.time()
        asyncio.ensure_future(run_gstreamer_stream(pipeline, track, curr_time))


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
    global pc, relay
    
    pc = RTCPeerConnection()
    relay = MediaRelay()

    @pc.on("track")
    def on_tack(track: MediaStreamTrack):
        init_incomming_track(relay.subscribe(track))

    @pc.on("connectionstatechange")
    def on_connection_state_change():
        start_recording(pc.connectionState)

    async with serve(echo, "0.0.0.0", 3000) as server:
        await server.serve_forever()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format=LOGGIN_FORMAT)

    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
