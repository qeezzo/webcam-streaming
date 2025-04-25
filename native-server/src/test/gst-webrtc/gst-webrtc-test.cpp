#define GST_USE_UNSTABLE_API

#include <gst/gst.h>
#include <gst/sdp/sdp.h>
#include <gst/webrtc/webrtc.h>

#include <iostream>

struct GstWebRTCContext {
    GstElement* pipeline;
    GstElement* webrtcbin;
};

void create_sdp_offer(GstPromise* promise, gpointer udata) {
    GstWebRTCSessionDescription *offer = NULL;
    const GstStructure *reply;

    g_assert_cmphex(gst_promise_wait(promise), ==, GST_PROMISE_RESULT_REPLIED);
    reply = gst_promise_get_reply(promise);
    gst_structure_get(reply, "offer", GST_TYPE_WEBRTC_SESSION_DESCRIPTION, &offer, NULL);
    gst_promise_unref(promise);

    std::cout << gst_sdp_message_as_text(offer->sdp) << gst_webrtc_sdp_type_to_string(offer->type) << std::endl;
}

void on_negotiation_needed(GstElement* object, gpointer udata) {
    GstWebRTCContext* context = static_cast<GstWebRTCContext*>(udata);
    GstPromise* promise = gst_promise_new_with_change_func(create_sdp_offer, context, NULL);
    g_signal_emit_by_name(context->webrtcbin, "create-offer", NULL, promise);
}

int init_webrtc_pipeline(GstWebRTCContext* context) {
    if (!context) {
        g_printerr("GstContext is NULL\n");
        return -1;
    }

    context->pipeline = gst_pipeline_new("webrtc-pipeline");
    context->webrtcbin = gst_element_factory_make("webrtcbin", "recv");

    if (!(context->pipeline && context->webrtcbin)) {
        g_printerr("Webrtc pipeline initialization failed\n");
        return -1;
    }

    GstWebRTCRTPTransceiver* transceiver = NULL;
    GstCaps* caps = gst_caps_new_simple ("application/x-rtp",
        "media", G_TYPE_STRING, "video",
        "encoding-name", G_TYPE_STRING, "H264",
        "payload", G_TYPE_INT, 96,
        "clock-rate", G_TYPE_INT, 90000,
        "rtcp-fb-nack-pli", G_TYPE_BOOLEAN, TRUE,
        "rtcp-fb-ccm-fir", G_TYPE_BOOLEAN, TRUE,
        "rtcp-fb-transport-cc", G_TYPE_BOOLEAN, TRUE,
        "ssrc", G_TYPE_UINT, 10000,
        "extmap-1", G_TYPE_STRING, "urn:ietf:params:rtp-hdrext:sdes:mid",
        NULL
    );

    g_signal_connect(context->webrtcbin, "on-negotiation-needed", G_CALLBACK(on_negotiation_needed), context);
    g_signal_emit_by_name(context->webrtcbin, "add-transceiver", GST_WEBRTC_RTP_TRANSCEIVER_DIRECTION_RECVONLY, caps, &transceiver);

    gst_bin_add_many(GST_BIN(context->pipeline), context->webrtcbin, NULL);
    GstStateChangeReturn ret = gst_element_set_state (context->pipeline, GST_STATE_PLAYING);
    if (ret == GST_STATE_CHANGE_FAILURE) {
        g_printerr("Unable to set the pipeline to the playing state\n");
        gst_object_unref(context->pipeline);
        return -1;
    }

    return 0;
}

int main(int argc, char* argv[]) {
    GstWebRTCContext webrtc_context;
    GError *error = NULL;

    GOptionContext* gcontext = g_option_context_new("- gstreamer webrtc sendrecv demo");
    g_option_context_add_group(gcontext, gst_init_get_option_group());
    if (!g_option_context_parse(gcontext, &argc, &argv, &error)){
        g_printerr("Error initializing: %s\n", error->message);
        return -1;
    }

    if (init_webrtc_pipeline(&webrtc_context) < 0) {
        return -1;
    }

    while(true);
    return 0;
}