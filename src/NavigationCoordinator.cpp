#include "NavigationCoordinator.h"

NavigationCoordinator::NavigationCoordinator()
{

}

NavigationCoordinator::~NavigationCoordinator()
{
    //dtor
    digitalWrite(0, LOW);
    digitalWrite(1, LOW);

    digitalWrite(3, LOW);
    digitalWrite(4, LOW);
    digitalWrite(5, LOW);
}

void NavigationCoordinator::UpdateNavigationParameters(DIRECTION navigationParameter)
{
    _pendingUpdates.push(navigationParameter);
}

void NavigationCoordinator::Accelerate()
{
    mvprintw(0, 0, "Accellerated\n");

    NotifyPin(AcceleratePin);
}

void NavigationCoordinator::Decelerate()
{
    mvprintw(0, 0, "Decelerated\n");

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

void NavigationCoordinator::Stop()
{
    mvprintw(0, 0, "Stopped\n");

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
        pinMode(0, OUTPUT);
        pinMode(1, OUTPUT);

        pinMode(3, OUTPUT);
        pinMode(4, OUTPUT);
    }
}
