#include <stdio.h>
#include <iostream>
#include <thread>
#include <string>
#include <curses.h>
#include <wiringPi.h>
#include <softPwm.h>

#include "NavigationCoordinator.h"
#include "CommunicationManager.h"

using namespace std;

int main(void)
{
    if (argc != 2)
    {
        std::cerr << "Usage: <host>" << std::endl;
        return 1;
    }

    if(wiringPiSetup() == -1)
    {
        printw("Could not initialize wiring pi");
    }

    pinMode(0, OUTPUT);

    NavigationCoordinator navigationCoordinator;
    navigationCoordinator.Start();

    CommunicationManager communicationManager;
    communicationManager.Start();

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

