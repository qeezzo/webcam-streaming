cmake_minimum_required(VERSION 3.8)
project(gst-webrtc-server)

set(CMAKE_CXX_STANDARD 17)
set(SRC_FILES src/main.cpp)

add_executable(gst-webrtc-server ${SRC_FILES})

find_package(PkgConfig)
if (PKG_CONFIG_FOUND)
    pkg_check_modules(GST REQUIRED glib-2.0 gstreamer-1.0 gstreamer-sdp-1.0 gstreamer-webrtc-1.0 libwebsockets)
    if (GST_FOUND)
        target_link_libraries(gst-webrtc-server ${GST_LIBRARIES})
        add_definitions(${GST_CFLAGS} ${GST_CFLAGS_OTHER})
    endif()
endif()