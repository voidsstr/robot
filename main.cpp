#include <stdio.h>
#include <iostream>
#include "NavigationCoordinator.h"
#include <string>
#include <curses.h>
#include <wiringPi.h>
#include <softPwm.h>

using namespace std;

int main(void)
{
    if(wiringPiSetup() == -1)
    {
        printw("Could not initialize wiring pi");
    }

    pinMode(0, OUTPUT);

    NavigationCoordinator navigationCoordinator;
    navigationCoordinator.Start();

    InputProcessor inputProcessor;

    initscr();
    raw();
    keypad(stdscr, TRUE);
    noecho();

    int ch;

    digitalWrite(0, LOW);

    while(true) {
        ch = getch();

        DIRECTION input = inputProcessor.ProcessInput(ch);
        navigationCoordinator.UpdateNavigationParameters(input);
        navigationCoordinator.ProcessUpdate();
    }

	return 0;
}

