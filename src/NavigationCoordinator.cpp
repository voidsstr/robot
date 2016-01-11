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
}

void NavigationCoordinator::UpdateNavigationParameters(DIRECTION navigationParameter)
{
    _pendingUpdates.push(navigationParameter);
}

void NavigationCoordinator::Accelerate()
{
    mvprintw(0, 0, "Accellerated\n");

    digitalWrite(1, HIGH);
    delay(10);
    digitalWrite(1, LOW);
}

void NavigationCoordinator::Decelerate()
{
    mvprintw(0, 0, "Decelerated\n");

    digitalWrite(0, HIGH);
    delay(10);
    digitalWrite(0, LOW);
}

void NavigationCoordinator::RotateRight()
{
    mvprintw(0, 0, "Rotated right\n");

    digitalWrite(3, HIGH);
    delay(10);
    digitalWrite(3, LOW);
}

void NavigationCoordinator::RotateLeft()
{
    mvprintw(0, 0, "Rotated left\n");

    digitalWrite(4, HIGH);
    delay(10);
    digitalWrite(4, LOW);
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
