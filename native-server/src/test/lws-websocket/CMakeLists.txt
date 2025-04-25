set(JSON_BuildTests OFF CACHE INTERNAL "")

add_executable(lws-test lws-test.cpp)

find_package(PkgConfig)
if (PKG_CONFIG_FOUND)
    pkg_check_modules(LWS REQUIRED libwebsockets)
    if (LWS_FOUND)
        target_link_libraries(lws-test PRIVATE ${LWS_LIBRARIES})
        add_definitions(${LWS_CFLAGS} ${LWS_CFLAGS_OTHER})
    endif()
endif()

target_link_libraries(lws-test PRIVATE nlohmann_json::nlohmann_json)