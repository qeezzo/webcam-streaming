#include <libwebsockets.h>
#include <signal.h>

#include <iostream>
#include <string>

static int interrupted = 0;

static struct lws_protocols protocols[] = {
    {"ws", callback_echo, 0, 0},
    {NULL, NULL, 0, 0}
};

void sigint_handler(int sig) {
	interrupted = 1;
}

void handle_incomming_message(const std::string& message) {
    
}

int callback_echo(struct lws* wsi, enum lws_callback_reasons reason, void* user, void* in, size_t len) {
    static std::string message;

    switch (reason) {
        case LWS_CALLBACK_RECEIVE:
            message += std::string(static_cast<const char*>(in), len);
            if (lws_is_final_fragment(wsi) != 0) {
                lwsl_user("Received: (%d-%llu)%s\n", lws_is_final_fragment(wsi), message.size(), message.data());
                handle_incomming_message(message);
                message.resize(0);
            }
            break;
        default:
            break;
    }
    return 0;
}

int main(int argc, char* argv[]) {
    lws_context_creation_info info;
    lws_context* context;

    const char *p;
	int n = 0, logs = LLL_USER | LLL_ERR | LLL_WARN | LLL_NOTICE;

    signal(SIGINT, sigint_handler);

    lws_set_log_level(logs, NULL);
    lwsl_user("LWS minimal ws server\n");

	memset(&info, 0, sizeof(info));
	info.port = 3000;
	info.protocols = protocols;
	info.vhost_name = "localhost";

    context = lws_create_context(&info);
	if (!context) {
		lwsl_err("lws init failed\n");
		return 1;
	}

    
	while (n >= 0 && !interrupted) {
        n = lws_service(context, 0);
    }

    lws_context_destroy(context);

    return 0;
}