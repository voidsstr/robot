#include <stdio.h>
#include <iostream>
#include <string>
#include <curses.h>
#include <wiringPi.h>
#include <softPwm.h>

#include "NavigationCoordinator.h"
#include "CommunicationManager.h"

using namespace std;

int main(int argc, char* argv[])
{
    if (argc != 3)
    {
        mvprintw(0, 0, "Usage: <host> <port>\n");
        return 1;
    }

    initscr();
    raw();
    keypad(stdscr, TRUE);
    noecho();

    NavigationCoordinator navigationCoordinator;
    navigationCoordinator.Start();

    CommunicationManager communicationManager;
    communicationManager.Start(argv[1], argv[2]);

    InputProcessor inputProcessor;

    int ch;

    while(true) {
        ch = getch();

        DIRECTION input = inputProcessor.ProcessInput(ch);
        navigationCoordinator.UpdateNavigationParameters(input);
        navigationCoordinator.ProcessUpdate();
    }

	return 0;
}

