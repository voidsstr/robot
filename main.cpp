#include <stdio.h>
#include <iostream>
#include "NavigationCoordinator.h"
#include <thread>
#include <string>
#include <curses.h>

using namespace std;

#define LedPin    1

void InitiateNavigation(NavigationCoordinator* navigationCoordinator)
{
    //navigationCoordinator->Start();
}

int main(void)
{
    //NavigationCoordinator navigationCoordinator;
    InputProcessor inputProcessor;

    //thread t1(InitiateNavigation, &navigationCoordinator);

    initscr();
    raw();
    keypad(stdscr, TRUE);
    noecho();

    int ch;

    printw("Use the arrow keys to control the bot.\n");

    while(true) {
        ch = getch();

        NavigationParameter* input = inputProcessor.ProcessInput(ch);
    }

	return 0;
}

