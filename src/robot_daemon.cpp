#include <iostream>
#include <csignal>
#include <unistd.h>
#include <cstring>

#include "NavigationCoordinator.h"
#include "WifiCommandServer.h"

// Global pointers for signal handler
WifiCommandServer* g_server = nullptr;
NavigationCoordinator* g_navCoordinator = nullptr;
volatile bool g_running = true;

void signalHandler(int signum)
{
    std::cout << "\nReceived signal " << signum << ", shutting down..." << std::endl;
    g_running = false;

    if (g_server)
    {
        g_server->stop();
    }
}

void printUsage(const char* programName)
{
    std::cout << "Usage: " << programName << " [options]" << std::endl;
    std::cout << "Options:" << std::endl;
    std::cout << "  -p <port>    TCP port to listen on (default: 8080)" << std::endl;
    std::cout << "  -s <serial>  Arduino serial port (default: /dev/ttyACM0)" << std::endl;
    std::cout << "  -h           Show this help message" << std::endl;
}

int main(int argc, char* argv[])
{
    int port = 8080;
    std::string serialPort = "/dev/ttyACM0";

    // Parse command line arguments
    for (int i = 1; i < argc; i++)
    {
        if (strcmp(argv[i], "-p") == 0 && i + 1 < argc)
        {
            port = atoi(argv[++i]);
        }
        else if (strcmp(argv[i], "-s") == 0 && i + 1 < argc)
        {
            serialPort = argv[++i];
        }
        else if (strcmp(argv[i], "-h") == 0)
        {
            printUsage(argv[0]);
            return 0;
        }
    }

    // Set up signal handlers
    signal(SIGINT, signalHandler);
    signal(SIGTERM, signalHandler);

    std::cout << "==================================" << std::endl;
    std::cout << "    Robot WiFi Control Daemon" << std::endl;
    std::cout << "==================================" << std::endl;
    std::cout << "Serial Port: " << serialPort << std::endl;
    std::cout << "TCP Port:    " << port << std::endl;
    std::cout << std::endl;

    // Initialize navigation coordinator (connects to Arduino)
    NavigationCoordinator navCoordinator;
    g_navCoordinator = &navCoordinator;

    std::cout << "Connecting to Arduino..." << std::endl;

    // Try to connect to Arduino
    bool arduinoConnected = false;
    for (int attempt = 1; attempt <= 5; attempt++)
    {
        std::cout << "Attempt " << attempt << "/5..." << std::endl;

        // For daemon mode, we don't use curses
        // Directly try to connect via serial
        if (navCoordinator.Start(serialPort))
        {
            arduinoConnected = true;
            break;
        }

        sleep(2);
    }

    if (!arduinoConnected)
    {
        std::cerr << "Failed to connect to Arduino after 5 attempts" << std::endl;
        std::cerr << "Continuing without Arduino connection (commands will be logged only)" << std::endl;
    }
    else
    {
        std::cout << "Arduino connected successfully!" << std::endl;
    }

    // Start WiFi command server
    WifiCommandServer server(port);
    g_server = &server;

    if (!server.start(&navCoordinator))
    {
        std::cerr << "Failed to start WiFi command server" << std::endl;
        return 1;
    }

    std::cout << std::endl;
    std::cout << "Daemon running. Press Ctrl+C to stop." << std::endl;
    std::cout << "Listening for connections on port " << port << std::endl;
    std::cout << std::endl;

    // Main loop - just wait for signals
    while (g_running)
    {
        sleep(1);
    }

    std::cout << "Shutting down..." << std::endl;

    server.stop();

    std::cout << "Goodbye!" << std::endl;

    return 0;
}
