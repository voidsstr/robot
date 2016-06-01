#include "NavigationCoordinator.h"

NavigationCoordinator::NavigationCoordinator()
{
    mvprintw(0, 0, "Telemetry Activated. No commands recieved.\n");
}

NavigationCoordinator::~NavigationCoordinator()
{
    //dtor
    digitalWrite(AcceleratePin, LOW);
    digitalWrite(DecelleratePin, LOW);

    digitalWrite(RotateRightPin, LOW);
    digitalWrite(RotateLeftPin, LOW);
    digitalWrite(StopPin, LOW);
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
    mvprintw(0, 0, "Accellerated\n");
    _navigationCount++;
    NotifyPin(AcceleratePin);
}

void NavigationCoordinator::Decelerate()
{
    mvprintw(0, 0, "Decelerated\n");
    _navigationCount--;
    NotifyPin(DecelleratePin);
}

void NavigationCoordinator::RotateRight()
{
    mvprintw(0, 0, "Rotated right\n");

    NotifyPin(RotateRightPin);
}

void NavigationCoordinator::RotateLeft()
{
    mvprintw(0, 0, "Rotated left\n");

    NotifyPin(RotateLeftPin);
}

void NavigationCoordinator::StopRobot()
{
    _navigationCount = 0;
    NotifyPin(StopPin);
}

void NavigationCoordinator::NotifyPin(int pin)
{
    digitalWrite(pin, HIGH);
    delay(10);
    digitalWrite(pin, LOW);
}

void NavigationCoordinator::ProcessUpdate()
{
    if(_pendingUpdates.size() > 0) {
        while(_pendingUpdates.size() > 0) {
            DIRECTION currentUpdate = _pendingUpdates.top();
            _pendingUpdates.pop();

            if(currentUpdate == DIRECTION::UP) {
                Accelerate();
            }
            else if(currentUpdate == DIRECTION::DOWN) {
                Decelerate();
            }
            else if(currentUpdate == DIRECTION::LEFT) {
                RotateLeft();
            }
            else if(currentUpdate == DIRECTION::RIGHT) {
                RotateRight();
            }
            else {
                mvprintw(0, 0, "No movement\n");
            }
        }
    }
}

void NavigationCoordinator::Start()
{
	if(wiringPiSetup() == -1){ //when initialize wiring failed,print messageto screen
		printw("setup wiringPi failed !\n");
	}
    else {
        pinMode(AcceleratePin, OUTPUT);
        pinMode(DecelleratePin, OUTPUT);

        pinMode(RotateRightPin, OUTPUT);
        pinMode(RotateLeftPin, OUTPUT);

        pinMode(StopPin, OUTPUT);
    }
}
