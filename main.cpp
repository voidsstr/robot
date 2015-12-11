#include <stdio.h>
#include <iostream>
#include "NavigationCoordinator.h"
#include <thread>
#include <string>
#include <curses.h>

using namespace std;

#define LedPin    1

int main(void)
{
    NavigationCoordinator navigationCoordinator;
    navigationCoordinator.Start();

    InputProcessor inputProcessor;

    initscr();
    raw();
    keypad(stdscr, TRUE);
    noecho();

    int ch;

    printw("Use the arrow keys to control the bot.\n");

    while(true) {
        ch = getch();

        DIRECTION input = inputProcessor.ProcessInput(ch);
        navigationCoordinator.UpdateNavigationParameters(input);
        navigationCoordinator.ProcessUpdate();
    }

	return 0;
}

