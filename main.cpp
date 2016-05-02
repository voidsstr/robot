#include <stdio.h>
#include <stdlib.h>
#include <iostream>
#include <string>
#include <curses.h>
#include <wiringPi.h>
#include <softPwm.h>

#include "NavigationCoordinator.h"
#include "RobotCommunicationManager.h"
#include "RelayServer.h"
#include "ClientManager.h"
#include "LidarManager.h"

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

    InputProcessor inputProcessor;

    RobotCommunicationManager communicationManager;
    NavigationCoordinator navigationCoordinator;
    navigationCoordinator.Start();

    /*Start listenining to commands from server*/
    communicationManager.ConnectToRelayServer(ipAddress, ROBOT_RELAY_LISTEN_PORT, &navigationCoordinator, &inputProcessor);

    int ch;

    while(true)
    {
        ch = getch();

        DIRECTION input = inputProcessor.ProcessInput(ch);

        if(input != DIRECTION::UNKNOWN)
        {
            //Directly control robot
            navigationCoordinator.UpdateNavigationParameters(input);
            navigationCoordinator.ProcessUpdate();
        }
    }
}

void clientLoop(char* ipAddress)
{
    setupCurses();

    InputProcessor inputProcessor;

    int ch;

    mvprintw(0, 0, "Enter commands to send to robot...\n");

    ClientManager client(ipAddress, CLIENT_RELAY_LISTEN_PORT);

    while(true)
    {
        ch = getch();

        DIRECTION input = inputProcessor.ProcessInput(ch);

        if(input != DIRECTION::UNKNOWN)
        {
            int message[2] = { 1, ch };
            //Send command to robot via network
            client.SendMessage(message);
        }
    }
}

void lidarLoop()
{
    LidarManager manager;
    manager.InitiateDataCollection();
    manager.CheckProximity();
}

/*
Usage: <executable> <mode>
*/
int main(int argc, char* argv[])
{
    bool isRobot = strcmp(argv[1], "robot") == 0;
    bool isClient = strcmp(argv[1], "client") == 0;
    bool isRelayServer = strcmp(argv[1], "server") == 0;
    bool isLidar = strcmp(argv[1], "lidar") == 0;

    if(isRobot)
    {
        robotLoop(argv[2]);
    }
    else if(isClient)
    {
        //TODO: implement client loop compatible with relay server
        clientLoop(argv[2]);
    }
    else if(isRelayServer)
    {
        //TODO: test recieving of data at relay server
        RelayServer relayServer(CLIENT_RELAY_LISTEN_PORT, ROBOT_RELAY_LISTEN_PORT);
        relayServer.Start();
    }
    else if(isLidar)
    {
        lidarLoop();
    }

	return 0;
}

