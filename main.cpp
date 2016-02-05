#include <stdio.h>
#include <iostream>
#include <string>
#include <curses.h>
#include <wiringPi.h>
#include <softPwm.h>

#include "NavigationCoordinator.h"
#include "CommunicationManager.h"

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

void robotLoop(InputProcessor* inputProcessor, NavigationCoordinator* navigationCoordinator, CommunicationManager* communicationManager)
{
    setupCurses();

    int ch;

    while(true)
    {
        ch = getch();

        DIRECTION input = inputProcessor->ProcessInput(ch);

        if(input != DIRECTION::UNKNOWN)
        {
            //Directly control robot
            navigationCoordinator->UpdateNavigationParameters(input);
            navigationCoordinator->ProcessUpdate();
        }
    }
}

void clientLoop(InputProcessor* inputProcessor, CommunicationManager* communicationManager)
{
    setupCurses();

    int ch;

    mvprintw(0, 0, "Enter commands to send to robot...\n");

    while(true)
    {
        ch = getch();

        DIRECTION input = inputProcessor->ProcessInput(ch);

        if(input != DIRECTION::UNKNOWN)
        {
            //Send command to robot via network
            communicationManager->SendMessage(ch);
        }
    }
}

/*
Usage: <executable> <mode> <host?> <port?>
*/
int main(int argc, char* argv[])
{
    bool isRobot = strcmp(argv[1], "robot") == 0;
    bool isClient = strcmp(argv[1], "client") == 0;
    bool isRelayServer = strcmp(argv[1], "server") == 0;

    CommunicationManager communicationManager;
    InputProcessor inputProcessor;

    if(isRobot)
    {
        /*User is on console of physical robot*/
        NavigationCoordinator navigationCoordinator;
        navigationCoordinator.Start();

        /*Start listenining to commands from server*/
        communicationManager.Connect(argv[2], argv[3], &navigationCoordinator, &inputProcessor);

        robotLoop(&inputProcessor, &navigationCoordinator, &communicationManager);
    }
    else if(isClient)
    {
        clientLoop(&inputProcessor, &communicationManager);
    }
    else if(isRelayServer)
    {
        communicationManager.StartRelayServer();
    }

	return 0;
}

