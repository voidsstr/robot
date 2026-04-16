#include "NavigationCoordinator.h"
#include <curses.h>

NavigationCoordinator::NavigationCoordinator()
    : _navigationCount(0)
{
    mvprintw(0, 0, "Telemetry Activated. No commands received.\n");
}

NavigationCoordinator::~NavigationCoordinator()
{
    // Ensure all pins are LOW on destruction
    #ifdef __arm__
    digitalWrite(AcceleratePin, LOW);
    digitalWrite(DecelleratePin, LOW);
    digitalWrite(RotateRightPin, LOW);
    digitalWrite(RotateLeftPin, LOW);
    digitalWrite(StopPin, LOW);
    #endif
}

bool NavigationCoordinator::IsMovingBackward()
{
    return _navigationCount < 0;
}

bool NavigationCoordinator::IsMovingForward()
{
    return _navigationCount > 0;
}

void NavigationCoordinator::UpdateNavigationParameters(DIRECTION navigationParameter)
{
    _pendingUpdates.push(navigationParameter);
}

void NavigationCoordinator::PrintTelemetry()
{
    mvprintw(2, 0, "Speed: %i     ", _navigationCount);
}

void NavigationCoordinator::Accelerate()
{
    mvprintw(0, 0, "Accelerated                    \n");
    _navigationCount++;
    NotifyPin(AcceleratePin);
}

void NavigationCoordinator::Decelerate()
{
    mvprintw(0, 0, "Decelerated                    \n");
    _navigationCount--;
    NotifyPin(DecelleratePin);
}

void NavigationCoordinator::RotateRight()
{
    mvprintw(0, 0, "Rotated right                  \n");
    NotifyPin(RotateRightPin);
}

void NavigationCoordinator::RotateLeft()
{
    mvprintw(0, 0, "Rotated left                   \n");
    NotifyPin(RotateLeftPin);
}

void NavigationCoordinator::StopRobot()
{
    mvprintw(0, 0, "Stopped                        \n");
    _navigationCount = 0;
    NotifyPin(StopPin);
}

void NavigationCoordinator::NotifyPin(int pin)
{
    #ifdef __arm__
    digitalWrite(pin, HIGH);
    delay(10);  // 10ms pulse
    digitalWrite(pin, LOW);
    #endif
}

void NavigationCoordinator::ProcessUpdate()
{
    if (_pendingUpdates.size() > 0)
    {
        while (_pendingUpdates.size() > 0)
        {
            DIRECTION currentUpdate = _pendingUpdates.top();
            _pendingUpdates.pop();

            if (currentUpdate == DIRECTION::UP)
            {
                Accelerate();
            }
            else if (currentUpdate == DIRECTION::DOWN)
            {
                Decelerate();
            }
            else if (currentUpdate == DIRECTION::LEFT)
            {
                RotateLeft();
            }
            else if (currentUpdate == DIRECTION::RIGHT)
            {
                RotateRight();
            }
            else if (currentUpdate == DIRECTION::STOP)
            {
                StopRobot();
            }
            else
            {
                mvprintw(0, 0, "No movement                    \n");
            }
        }
    }
}

void NavigationCoordinator::Start()
{
    #ifdef __arm__
    if (wiringPiSetup() == -1)
    {
        printw("Failed to setup wiringPi!\n");
        return;
    }

    // Configure all pins as OUTPUT
    pinMode(AcceleratePin, OUTPUT);
    pinMode(DecelleratePin, OUTPUT);
    pinMode(RotateRightPin, OUTPUT);
    pinMode(RotateLeftPin, OUTPUT);
    pinMode(StopPin, OUTPUT);

    // Ensure all pins start LOW
    digitalWrite(AcceleratePin, LOW);
    digitalWrite(DecelleratePin, LOW);
    digitalWrite(RotateRightPin, LOW);
    digitalWrite(RotateLeftPin, LOW);
    digitalWrite(StopPin, LOW);

    printw("GPIO initialized successfully.\n");
    #else
    printw("GPIO not available (not running on ARM).\n");
    #endif
}
