cmake_minimum_required(VERSION 3.8)
project(gst-webrtc-server)

set(CMAKE_CXX_STANDARD 17)

add_subdirectory(src/test/gst-webrtc test/gst-webrtc)
add_subdirectory(src/test/lws-websocket test/lws-websocket)