#include <stdio.h>
#include <iostream>
#include "NavigationCoordinator.h"
#include <thread>
#include <string>
#include <curses.h>

using namespace std;

#define LedPin    1

void InitiateNavigation()
{
    NavigationCoordinator coordinator;

    coordinator.Start();
}

int main(void)
{
    thread t1(InitiateNavigation);

    initscr();
    raw();
    keypad(stdscr, TRUE);
    noecho();

    int ch;

    printw("Use the arrow keys to control the bot.");

    while(true) {
        ch = getch();

        if(ch == KEY_UP) {
            printw("Up pressed!\n");
        }
    }

	return 0;
}

