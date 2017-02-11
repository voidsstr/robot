#include <stdio.h>
#include <stdlib.h>
#include <iostream>
#include <string>
#include <curses.h>
#include <wiringPi.h>
#include <softPwm.h>
#include <unistd.h>

#include "NavigationCoordinator.h"
#include "RobotCommunicationManager.h"
#include "RadioCommunicationManager.h"
#include "RelayServer.h"
#include "ClientManager.h"
#include "LidarManager.h"
#include "FaceTargetPerceptron.h"
#include "HUDManager.h"

using namespace std;

void setupCurses()
{
    WINDOW *w = initscr();
    cbreak();
    nodelay(w, TRUE);
    raw();
    keypad(stdscr, TRUE);
    noecho();
}

void robotLoop()
{
    setupCurses();

    NavigationCoordinator navigationCoordinator;
    navigationCoordinator.Start();

    RadioCommunicationManager radio(Recieve);
    radio.startRadio();

    while(true)
    {
        int nBufferSize = 64;
        char cBuffer[nBufferSize];
        int nBytesRead = nBufferSize;

        while(true)
        {
            CCRTPPacket* packet = radio.waitForPacket();

            if(packet != NULL && packet->dataLength() > 0)
            {
                int data = atoi(packet->data());

                navigationCoordinator.UpdateNavigationParameters((DIRECTION)data);
                navigationCoordinator.ProcessUpdate();
            }

            usleep(100000);
        }
    }
}

void clientLoop()
{
    setupCurses();

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
            char* command = (char*)oss.str().c_str();

            CCRTPPacket* packet = new CCRTPPacket(command, sizeof(command), 1);

            radio.sendPacket(packet, true);
        }
    }
}

/*
Usage: <executable> <mode>
*/
int main(int argc, char* argv[])
{
    bool isRobot = strcmp(argv[1], "robot") == 0;
    bool isClient = strcmp(argv[1], "client") == 0;

    if(isRobot)
    {
        robotLoop();
    }
    else if(isClient)
    {
        clientLoop();
    }

    return 0;
}

