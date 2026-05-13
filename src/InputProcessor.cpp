#include "InputProcessor.h"

InputProcessor::InputProcessor()
{
    //ctor
}

InputProcessor::~InputProcessor()
{
    //dtor
}

// Map a single ncurses keycode to a DIRECTION command.  Accepts both the
// arrow keys (ncurses KEY_UP / KEY_DOWN / KEY_LEFT / KEY_RIGHT) and WASD,
// so the local Pi console feels the same as the wifi_client terminal.
// Space and 'X' are both wired to STOP for panic-stop with either thumb.
DIRECTION InputProcessor::ProcessInput(int key)
{
    switch (key)
    {
        case KEY_UP:    case 'w': case 'W':
            HUDManager::logMessage(InputFeedback, "Navigated forward");
            return DIRECTION::UP;

        case KEY_DOWN:  case 's': case 'S':
            HUDManager::logMessage(InputFeedback, "Navigated backward");
            return DIRECTION::DOWN;

        case KEY_LEFT:  case 'a': case 'A':
            HUDManager::logMessage(InputFeedback, "Navigated left");
            return DIRECTION::LEFT;

        case KEY_RIGHT: case 'd': case 'D':
            HUDManager::logMessage(InputFeedback, "Navigated right");
            return DIRECTION::RIGHT;

        case ' ':       case 'x': case 'X':
            HUDManager::logMessage(InputFeedback, "Stopped robot");
            return DIRECTION::STOP;

        default:
            return DIRECTION::UNKNOWN;
    }
}
