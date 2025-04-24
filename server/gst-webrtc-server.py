import json
import asyncio
import logging
import gi
import time
import dis

from websockets.asyncio.server import serve 
from websockets.asyncio.server import ServerConnection

gi.require_version("Gst", "1.0")
from gi.repository import Gst

gi.require_version("GstWebRTC", "1.0")
from gi.repository import GstWebRTC

gi.require_version("GstSdp", "1.0")
from gi.repository import GstSdp


LOGGIN_FORMAT = "%(levelname)s (%(filename)s:%(lineno)d) %(message)s"


media_pipeline: Gst.Element = None
webrtc_handler: Gst.Bin = None


def on_ice_candidate(*_) -> None:
    logging.info("Call: on_ice_candidate()")


def on_incoming_stream(*_) -> None:
    logging.info("Call: on_incoming_stream()")


def on_data_channel(*_) -> None:
    logging.info("Call: on_data_channel()")


def prepare_data_channel(*_) -> None:
    logging.info("Call: prepare_data_channel()")


def on_connection_state_notify(*_) -> None:
    logging.info("Call: on_connection_state_notify()")


def on_ice_gathering_state_notify(*_) -> None:
    logging.info("Call: on_ice_gathering_state_notify()")


def on_test(promise: Gst.Promise, *_) -> None:
    assert promise.wait() == Gst.PromiseResult.REPLIED
    offer = promise.get_reply()
    offer = offer.get_value("offer")
    logging.info(f"SDP Offer(): {GstWebRTC.WebRTCSDPType.to_string(offer.type)}")


def create_sdp_offer(webrtc: Gst.Bin, websocket: ServerConnection) -> None:
    promise = Gst.Promise.new_with_change_func(on_test, None, None)
    webrtc.emit("create-offer", None, promise)


def init_webrtc_pipeline() -> tuple[Gst.Element, Gst.Bin]:
    logging.info("Call -> init_webrtc_pipeline()")
    Gst.init(None)

    pipeline = Gst.Pipeline.new("webrtc-pipeline")
    webrtc = Gst.ElementFactory.make("webrtcbin")

    if not (pipeline and webrtc):
        raise RuntimeError("Webrtc pipeline initialization failed")

    # webrtc.set_property("latency", 50)
    # webrtc.set_property("stun-server", "stun://stun.l.google.com:19302")

    pipeline.add(webrtc)

    webrtc.connect("on-negotiation-needed", lambda _ : create_sdp_offer(webrtc, None))
    webrtc.connect("on-ice-candidate", on_ice_candidate)
    webrtc.connect("pad-added", on_incoming_stream)
    webrtc.connect("on-data-channel", on_data_channel)
    webrtc.connect("prepare-data-channel", prepare_data_channel)
    webrtc.connect("notify::connection-state", on_connection_state_notify)
    webrtc.connect("notify::signaling-state", lambda webrtc, _ : logging.info(f"Signaling state -> { webrtc.get_property('signaling-state').value_nick }"))
    webrtc.connect("notify::ice-gathering-state", on_ice_gathering_state_notify)

    caps = Gst.caps_from_string("application/x-rtp,media=video,encoding-name=JPEG,payload=96,clock-rate=90000")
    webrtc.emit ("add-transceiver", GstWebRTC.WebRTCRTPTransceiverDirection.RECVONLY, caps)

    pipeline.set_state(Gst.State.READY)

    logging.info(f"WebRTC pipeline has started! ")

    return pipeline, webrtc


def create_sdp_answer(websocket: ServerConnection, json_sdp: any) -> None:
    logging.info(f"Sending SDP Answer: {json.dumps(json_sdp)}")


def accept_sdp_offer(websocket: ServerConnection, json_sdp: any) -> None:
    def on_create_answer(promise: Gst.Promise, *_) -> None:
        logging.info("Call -> on_create_answer()")
        assert promise.wait() == Gst.PromiseResult.REPLIED
        answer = promise.get_reply().get_value("answer")
        logging.info(f"Answer: {answer.sdp.as_text()}")
        promise = Gst.Promise.new()
        webrtc_handler.emit("set-local-description", answer, promise)
        promise.interrupt()

        # create_sdp_answer(None, {"sdp": {"type": "answer", "sdp": answer.sdp.as_text()}})


    def on_remote_desc_changed(promise: Gst.Promise, *_) -> None:
        logging.info("Call -> on_remote_desc_changed()")
        assert promise.wait() == Gst.PromiseResult.REPLIED
        promise = Gst.Promise.new_with_change_func(on_create_answer, None, None)
        webrtc_handler.emit("create-answer", None, promise)


    logging.info("Call -> accept_sdp_offer()")
    _, msg = GstSdp.sdp_message_new_from_text(json_sdp["sdp"])
    offer = GstWebRTC.WebRTCSessionDescription.new(GstWebRTC.WebRTCSDPType.OFFER, msg)
    promise = Gst.Promise.new_with_change_func(on_remote_desc_changed, None, None)
    webrtc_handler.emit("set-remote-description", offer, promise)


def accept_ice_candidate(json_ice: any) -> None:
    logging.info("Call -> accept_ice_candidate()")
    webrtc_handler.emit("add-ice-candidate", json_ice["ice"]["sdpMLineIndex"], json_ice["ice"]["candidate"])


async def echo(websocket: ServerConnection) -> None:
    async for message in websocket:
        json_msg = json.loads(message)
        if "sdp" in json_msg:
            logging.info(f"Accepting SDP Offer: {json.dumps(json_msg)}")
            # accept_sdp_offer(websocket, json_msg)
        elif "ice" in json_msg:
            logging.info(f"Accepting ICE Candidate: {json.dumps(json_msg)}")
            # accept_ice_candidate(json_msg)


async def main() -> None:
    global media_pipeline, webrtc_handler
    media_pipeline, webrtc_handler = init_webrtc_pipeline()

    async with serve(echo, "0.0.0.0", 3000) as server:
        await server.serve_forever()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format=LOGGIN_FORMAT)

    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())