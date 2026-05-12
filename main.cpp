#include <stdio.h>
#include <stdlib.h>
#include <iostream>
#include <string>
#include <curses.h>
#include <unistd.h>

#include "NavigationCoordinator.h"
#include "RobotCommunicationManager.h"
#include "RadioCommunicationManager.h"
#include "RelayServer.h"
#include "ClientManager.h"
#include "LidarManager.h"
#include "FaceTargetPerceptron.h"
#include "HUDManager.h"
#include "WifiCommandServer.h"

using namespace std;

void robotLoop()
{
    NavigationCoordinator navigationCoordinator;
    navigationCoordinator.Start();

    LidarManager lidarManager;
    lidarManager.InitiateDataCollection();

    RadioCommunicationManager radio(Recieve);
    bool radioUp = radio.startRadio();

    HUDManager::logMessage(UserInstruction, "Robot uplink started...");

    InputProcessor inputProcessor;

    HUDManager::logMessage(UserInstruction, "Options: Navigation (arrow keys), Stop (esc)");

    while(true)
    {
        std::unordered_map<int, float> perimeter = lidarManager.GetPerimeter();
        HUDManager::DrawLidarMap(50, 10, 10, perimeter, true, 20, true);

        if(radioUp)
        {
            // Process any pending requests sent via radio
            CCRTPPacket* packet = radio.waitForPacket();

            if(packet != NULL && packet->dataLength() > 0)
            {
                HUDManager::logMessage(InputFeedback, "Received data from client...");

                int data = atoi(packet->data());

                navigationCoordinator.UpdateNavigationParameters((DIRECTION)data);
                navigationCoordinator.ProcessUpdate();
            }
        }

        // Process any keyboard commands
        int ch = getch();

        DIRECTION input = inputProcessor.ProcessInput(ch);

        if(input != DIRECTION::UNKNOWN)
        {
            // Directly control robot
            navigationCoordinator.UpdateNavigationParameters(input);
            navigationCoordinator.ProcessUpdate();
        }
    }
}

void clientLoop()
{
    RadioCommunicationManager radio(Transmit);

    radio.startRadio();

    InputProcessor inputProcessor;

    HUDManager::logMessage(UserInstruction, "Client started. Options: Navigation (arrow keys), Stop (esc)");

    int ch;

    while(true)
    {
        ch = getch();

        DIRECTION input = inputProcessor.ProcessInput(ch);

        if(input != DIRECTION::UNKNOWN)
        {
            std::ostringstream oss;
            oss << input;
            std::string commandStr = oss.str();

            CCRTPPacket* packet = new CCRTPPacket(
                const_cast<char*>(commandStr.c_str()),
                commandStr.size(), 1);

            radio.sendPacket(packet, true);
        }
    }
}

void wifiServerLoop(int port)
{
    // Initialize curses for local display
    initscr();
    cbreak();
    noecho();
    keypad(stdscr, TRUE);
    nodelay(stdscr, TRUE);
    curs_set(0);

    NavigationCoordinator navigationCoordinator;
    navigationCoordinator.Start();

    mvprintw(3, 0, "GPIO initialized for motor control");

    // Start WiFi server
    WifiCommandServer server(port);
    if (!server.start(&navigationCoordinator))
    {
        endwin();
        cerr << "Failed to start WiFi server on port " << port << endl;
        return;
    }

    mvprintw(4, 0, "WiFi server listening on port %d", port);
    mvprintw(6, 0, "Press 'q' to quit");
    refresh();

    InputProcessor inputProcessor;

    // Main loop - handle local keyboard input too
    while(true)
    {
        int ch = getch();

        if (ch == 'q' || ch == 'Q')
        {
            break;
        }

        DIRECTION input = inputProcessor.ProcessInput(ch);

        if(input != DIRECTION::UNKNOWN)
        {
            navigationCoordinator.UpdateNavigationParameters(input);
            navigationCoordinator.ProcessUpdate();
        }

        usleep(10000);  // 10ms
    }

    server.stop();
    endwin();
}

void printUsage(const char* programName)
{
    cout << "Robot Control System" << endl;
    cout << endl;
    cout << "Usage: " << programName << " <mode> [options]" << endl;
    cout << endl;
    cout << "Modes:" << endl;
    cout << "  robot           Run as robot with radio receiver and local control" << endl;
    cout << "  client          Run as radio transmitter client" << endl;
    cout << "  wifi-server     Run as WiFi command server (for remote control)" << endl;
    cout << endl;
    cout << "WiFi Server Options:" << endl;
    cout << "  -p <port>       TCP port to listen on (default: 8080)" << endl;
    cout << endl;
    cout << "Examples:" << endl;
    cout << "  " << programName << " robot" << endl;
    cout << "  " << programName << " wifi-server -p 8080" << endl;
}

/*
Usage: <executable> <mode> [options]
*/
int main(int argc, char* argv[])
{
    if (argc < 2)
    {
        printUsage(argv[0]);
        return 1;
    }

    string mode = argv[1];

    if (mode == "robot")
    {
        robotLoop();
    }
    else if (mode == "client")
    {
        clientLoop();
    }
    else if (mode == "wifi-server")
    {
        int port = 8080;

        // Parse options
        for (int i = 2; i < argc; i++)
        {
            if (strcmp(argv[i], "-p") == 0 && i + 1 < argc)
            {
                port = atoi(argv[++i]);
            }
        }

        wifiServerLoop(port);
    }
    else if (mode == "-h" || mode == "--help")
    {
        printUsage(argv[0]);
    }
    else
    {
        cerr << "Unknown mode: " << mode << endl;
        printUsage(argv[0]);
        return 1;
    }

    return 0;
}
