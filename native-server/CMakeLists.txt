cmake_minimum_required(VERSION 3.8)
project(gst-webrtc-server)

set(CMAKE_CXX_STANDARD 17)

set(WEBRTC_SOURCE_DIR ${PROJECT_SOURCE_DIR}/src)
set(WEBRTC_THIRD_PARTY_DIR ${PROJECT_SOURCE_DIR}/3dparty)

add_subdirectory(${WEBRTC_THIRD_PARTY_DIR}/json)

add_subdirectory(${WEBRTC_SOURCE_DIR}/test/gst-webrtc test/gst-webrtc)
add_subdirectory(${WEBRTC_SOURCE_DIR}/test/lws-websocket test/lws-websocket)