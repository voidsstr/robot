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
        HUDManager::DrawLidarMap(50,10,10, perimeter, true, 20, true);

        if(radioUp)
        {
            //Process any pending requests sent via radio
            CCRTPPacket* packet = radio.waitForPacket();

            if(packet != NULL && packet->dataLength() > 0)
            {
                HUDManager::logMessage(InputFeedback, "Received data from client...");

                int data = atoi(packet->data());

                navigationCoordinator.UpdateNavigationParameters((DIRECTION)data);
                navigationCoordinator.ProcessUpdate();
            }
        }

        //Process any keyboard commands
        int ch = getch();

        DIRECTION input = inputProcessor.ProcessInput(ch);

        if(input != DIRECTION::UNKNOWN)
        {
            //Directly control robot
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

