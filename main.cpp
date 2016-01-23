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

    WINDOW *w = initscr();
    cbreak();
    nodelay(w, TRUE);
    raw();
    keypad(stdscr, TRUE);
    noecho();

    NavigationCoordinator navigationCoordinator;
    navigationCoordinator.Start();

    CommunicationManager communicationManager(&navigationCoordinator);
    communicationManager.Start(argv[1], argv[2]);

    InputProcessor inputProcessor;

    int ch;

    while(true) {
        ch = getch();

        DIRECTION input = inputProcessor.ProcessInput(ch);

        if(input != DIRECTION::UNKNOWN)
        {
            navigationCoordinator.UpdateNavigationParameters(input);
        }

        navigationCoordinator.ProcessUpdate();
    }

	return 0;
}

