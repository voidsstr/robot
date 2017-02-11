#include "InputProcessor.h"

InputProcessor::InputProcessor()
{
    //ctor
}

InputProcessor::~InputProcessor()
{
    //dtor
}

DIRECTION InputProcessor::ProcessInput(int key)
{
    if(key == 259)
    {
        HUDManager::logMessage(InputFeedback, "Navigated forward");
        return DIRECTION::UP;
    }
    else if(key == 258)
    {
        HUDManager::logMessage(InputFeedback, "Navigated backward");
        return DIRECTION::DOWN;
    }
    else if(key == 260)
    {
        HUDManager::logMessage(InputFeedback, "Navigated left");
        return DIRECTION::LEFT;
    }
    else if(key == 261)
    {
        HUDManager::logMessage(InputFeedback, "Navigated right");
        return DIRECTION::RIGHT;
    }
    else if(key == 113)
    {
        throw std::invalid_argument("Quit!");
    }

    return DIRECTION::UNKNOWN;
}
