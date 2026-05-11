# Robot Control System — build
#
# Common targets:
#   make            build all Pi binaries (robot, robot_daemon, wifi_client)
#   make robot      full robot binary (radio + LIDAR + wifi-server modes)
#   make daemon     robot_daemon — headless WiFi command server
#   make client     wifi_client — terminal control client
#   make arduino    compile the Arduino sketch with arduino-cli
#   make upload     compile + flash the Arduino  (override PORT=/dev/ttyUSB0, FQBN=...)
#   make deps       install all build dependencies (apt packages + arduino-cli + AVR core)
#   make clean
#
# Variables you may want to override:
#   FQBN=arduino:avr:nano   PORT=/dev/ttyUSB0   CXX=clang++

CXX      ?= g++
CXXSTD   ?= -std=c++14
OPT      ?= -O2
WARN     ?= -Wall
BIN      := bin

# Pick the vendored RPLIDAR SDK static lib that matches the host architecture.
ARCH := $(shell uname -m)
ifeq ($(ARCH),x86_64)
  RPLIDAR_LIBDIR := dependencies/lib/rplidar/x64
else ifneq (,$(filter aarch64 arm64,$(ARCH)))
  RPLIDAR_LIBDIR := dependencies/lib/rplidar/arm64
else ifneq (,$(filter i386 i686,$(ARCH)))
  RPLIDAR_LIBDIR := dependencies/lib/rplidar/x86
else
  RPLIDAR_LIBDIR := dependencies/lib/rplidar/x86
endif

INCLUDES := -I include -I dependencies/include
CXXFLAGS := $(OPT) $(WARN) $(CXXSTD) $(INCLUDES)

# --- source sets -------------------------------------------------------------
# Full "robot" binary: main.cpp + the units listed in Robot.cbp, plus the
# WifiCommandServer used by the wifi-server mode in main.cpp.
ROBOT_SRC := main.cpp \
  src/CCRTPPacket.cpp src/ClientManager.cpp src/FaceTargetPerceptron.cpp src/HUDManager.cpp \
  src/InputProcessor.cpp src/LidarManager.cpp src/NavigationCoordinator.cpp \
  src/RadioCommunicationManager.cpp src/RelayServer.cpp src/RobotCommunicationManager.cpp \
  src/RobotConnection.cpp src/WifiCommandServer.cpp
ROBOT_LIBS := -L $(RPLIDAR_LIBDIR) -lrplidar_sdk -lncurses -lboost_system -lusb-1.0 -lpthread -lrt

# robot_daemon: headless build of just the USB-serial + WiFi command path.
DAEMON_SRC := src/robot_daemon.cpp src/WifiCommandServer.cpp src/NavigationCoordinator.cpp \
  src/ArduinoSerialManager.cpp src/InputProcessor.cpp src/HUDManager.cpp
DAEMON_LIBS := -lncurses -lpthread

CLIENT_SRC  := src/wifi_client.cpp
CLIENT_LIBS := -lncurses -lpthread

# --- Arduino -----------------------------------------------------------------
SKETCH := src/Arduino/robot/robot.ino
FQBN   ?= arduino:avr:uno
PORT   ?= /dev/ttyACM0

.PHONY: all robot daemon client arduino upload deps clean
all: robot daemon client

robot:  $(BIN)/robot
daemon: $(BIN)/robot_daemon
client: $(BIN)/wifi_client

$(BIN):
	mkdir -p $(BIN)

$(BIN)/robot: $(ROBOT_SRC) | $(BIN)
	$(CXX) $(CXXFLAGS) -o $@ $(ROBOT_SRC) $(ROBOT_LIBS)

$(BIN)/robot_daemon: $(DAEMON_SRC) | $(BIN)
	$(CXX) $(CXXFLAGS) -o $@ $(DAEMON_SRC) $(DAEMON_LIBS)

$(BIN)/wifi_client: $(CLIENT_SRC) | $(BIN)
	$(CXX) $(CXXFLAGS) -o $@ $(CLIENT_SRC) $(CLIENT_LIBS)

# Arduino: compile-only (sanity check) and compile+flash.
arduino:
	arduino-cli compile --fqbn $(FQBN) $(SKETCH)

upload:
	arduino-cli compile --fqbn $(FQBN) $(SKETCH)
	arduino-cli upload  --fqbn $(FQBN) -p $(PORT) $(SKETCH)

deps:
	./scripts/install_deps.sh

clean:
	rm -f $(BIN)/robot $(BIN)/robot_daemon $(BIN)/wifi_client
