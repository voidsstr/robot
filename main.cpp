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

using namespace std;

int CLIENT_RELAY_LISTEN_PORT = 1337;
int ROBOT_RELAY_LISTEN_PORT = 1338;


void setupCurses()
{
    WINDOW *w = initscr();
    cbreak();
    nodelay(w, TRUE);
    raw();
    keypad(stdscr, TRUE);
    noecho();
}

void robotLoop(char* ipAddress)
{
    setupCurses();

    //InputProcessor inputProcessor;

    /*RobotCommunicationManager communicationManager;

    */

    /*LidarManager lidarManager;
    lidarManager.InitiateDataCollection();*/

    /*Start listenining to commands from server*/
    //communicationManager.ConnectToRelayServer(ipAddress, ROBOT_RELAY_LISTEN_PORT, &navigationCoordinator, &inputProcessor);

    //int ch;

    NavigationCoordinator navigationCoordinator;
    navigationCoordinator.Start();

    RadioCommunicationManager radio("radio://0/10/250K", Recieve);
    radio.startRadio();

    while(true)
    {
        //ch = getch();

        //DIRECTION input = inputProcessor.ProcessInput(ch);

        //usleep(100000);

        /*float objectAheadDistance = lidarManager.IsObjectAhead(8);
        float objectBehindDistance = lidarManager.IsObjectBehind(8);
        lidarManager.PrintScanData();*/

        /*#ifdef __arm__

        if((navigationCoordinator.IsMovingForward() && objectAheadDistance > 0)
                || (navigationCoordinator.IsMovingBackward() && objectBehindDistance > 0))
        {
            navigationCoordinator.StopRobot();
        }
        else
        {
            if(input != DIRECTION::UNKNOWN)
            {
                //Directly control robot
                navigationCoordinator.UpdateNavigationParameters(input);
                navigationCoordinator.ProcessUpdate();
            }
        }

        navigationCoordinator.PrintTelemetry();

        #endif*/

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

    RadioCommunicationManager radio("radio://0/10/250K", Transmit);

    radio.startRadio();

    InputProcessor inputProcessor;

    int ch;

    mvprintw(0, 0, "Enter commands to send to robot...\n");

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

void perceptronLoop()
{
    setupCurses();

    int ch;

    mvprintw(0, 0, "Press keys to simulate data...\n");
    FaceTargetPerceptron brain;

    std::vector<float> currentForces;
    currentForces.push_back(4);
    currentForces.push_back(8);

    float currentTheta = 55;

    mvprintw(0, 0, "Forces (L / R): %f / %f", currentForces[0], currentForces[1]);
    mvprintw(1, 0, "Current theta: %f", currentTheta);

    while(true)
    {
        ch = getch();

        std::vector<float> currentResult = brain.FeedForward(currentForces);
        std::vector<float>* error = brain.CalculateError(currentTheta, currentResult);

        mvprintw(0, 0, "Error values (L / R): %f / %f", error->at(0), error->at(1));
    }
}

/*
Usage: <executable> <mode>
*/
int main(int argc, char* argv[])
{
    bool isRobot = strcmp(argv[1], "robot") == 0;
    bool isClient = strcmp(argv[1], "client") == 0;
    bool isRelayServer = strcmp(argv[1], "server") == 0;
    bool isPerceptronTest = strcmp(argv[1], "nn") == 0;

    if(isRobot)
    {
        robotLoop(argv[2]);
    }
    else if(isClient)
    {
        //TODO: implement client loop compatible with relay server
        clientLoop();
    }
    else if(isRelayServer)
    {
        //TODO: test recieving of data at relay server
        RelayServer relayServer(CLIENT_RELAY_LISTEN_PORT, ROBOT_RELAY_LISTEN_PORT);
        relayServer.Start();
    }
    else if(isPerceptronTest)
    {
        perceptronLoop();
    }

    return 0;
}

