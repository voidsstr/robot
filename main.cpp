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
    mvprintw(6, 0, "Keys:  ARROWS / WASD = drive    SPACE / X = STOP    Q = quit");
    refresh();

    InputProcessor inputProcessor;

    // Main loop — handle local keyboard input too.
    //
    // IMPORTANT: getch() returns one buffered key per call.  Terminal auto-
    // repeat (~30 Hz) plus kernel/curses input buffering can queue more
    // keystrokes than we want to act on, and a naive "one getch per loop
    // tick" drains them long after the user lifted the key — every queued
    // event becomes another ±3 servo step the Arduino dutifully applies,
    // which is how you end up at full throttle a half-second after letting
    // go.  Fix: each loop tick, drain getch() to the LATEST event and act
    // only on that.  The motor step rate is then 1 cmd per loop tick, not
    // 1 per keypress, decoupled from how fast the terminal repeats.
    //
    // The tick is 50 ms (= one Servo PWM cycle at 50 Hz).  With ±3 per
    // command in robot.ino, that's a smooth ~1.5 s sweep from neutral to
    // full throttle while a direction key is held — fast enough to feel
    // responsive, slow enough to not overshoot.
    bool quit = false;
    while(!quit)
    {
        int ch;
        int latest = ERR;
        while((ch = getch()) != ERR)
        {
            if (ch == 'q' || ch == 'Q') { quit = true; break; }
            latest = ch;        // discard older events, keep only the newest
        }

        if(quit) break;

        if(latest != ERR)
        {
            DIRECTION input = inputProcessor.ProcessInput(latest);
            if(input != DIRECTION::UNKNOWN)
            {
                navigationCoordinator.UpdateNavigationParameters(input);
                navigationCoordinator.ProcessUpdate();
            }
        }

        // Live motor-level HUD — Pi-side mirror of the Arduino's servo
        // state, so the operator can see commands landing in real time.
        // 90 = neutral; <90 = forward; >90 = reverse.  Pulse width is the
        // approximate value the Sabertooth's R/C input sees.
        auto fmt = [](int level, char* buf, size_t n) {
            int us = 1000 + (int)(level * (1000.0 / 180.0) + 0.5);
            const char* dir = (level == 90) ? "stop" : (level < 90 ? "fwd " : "rev ");
            int pct = (level == 90) ? 0 : ((level < 90 ? 90 - level : level - 90) * 100 / 90);
            snprintf(buf, n, "%3d (%4dus, %s%3d%%)", level, us, dir, pct);
        };
        char lbuf[48], rbuf[48];
        fmt(navigationCoordinator.LeftLevel(),  lbuf, sizeof(lbuf));
        fmt(navigationCoordinator.RightLevel(), rbuf, sizeof(rbuf));
        mvprintw(8, 0, "Left  motor:  %s", lbuf);
        mvprintw(9, 0, "Right motor:  %s", rbuf);
        clrtoeol();
        refresh();

        usleep(50000);  // 50 ms = one servo PWM cycle; one motor step per tick
    }

    // Park the robot before tearing down curses — destructor sends 'S' too
    // when the serial fd closes, but doing it explicitly here means the
    // motors are at neutral the instant we leave the input loop.
    navigationCoordinator.UpdateNavigationParameters(DIRECTION::STOP);
    navigationCoordinator.ProcessUpdate();

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
