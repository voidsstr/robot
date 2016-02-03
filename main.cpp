#include <stdio.h>
#include <iostream>
#include <string>
#include <curses.h>
#include <wiringPi.h>
#include <softPwm.h>

#include "NavigationCoordinator.h"
#include "CommunicationManager.h"

using namespace std;

void robotLoop(InputProcessor* inputProcessor, NavigationCoordinator* navigationCoordinator, CommunicationManager* communicationManager)
{
    WINDOW *w = initscr();
    cbreak();
    nodelay(w, TRUE);
    raw();
    keypad(stdscr, TRUE);
    noecho();

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

void serverLoop(InputProcessor* inputProcessor, CommunicationManager* communicationManager)
{
    WINDOW *w = initscr();
    cbreak();
    nodelay(w, TRUE);
    raw();
    keypad(stdscr, TRUE);
    noecho();

    int ch;

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
    bool isRobot = argv[1] == "robot";

    CommunicationManager communicationManager;
    InputProcessor inputProcessor;

    if(isRobot)
    {
        /*User is on console of physical robot*/
        NavigationCoordinator navigationCoordinator;
        navigationCoordinator.Start();

        /*Start listenining to commands from server*/
        communicationManager.Connect(argv[2], argv[3], &navigationCoordinator);

        robotLoop(&inputProcessor, &navigationCoordinator, &communicationManager);
    }
    else
    {
        /*User is controlling the bot from a remote computer.
        This is done via intermidiate server*/
        communicationManager.StartListening();

        serverLoop(&inputProcessor, &communicationManager);
    }

	return 0;
}

