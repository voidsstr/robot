#include "HUDManager.h"

HUDManager::HUDManager()
{
    //ctor
}

HUDManager::~HUDManager()
{
    //dtor
}

void HUDManager::logMessage(enum MessageType messageType, std::string message)
{
    if(!message.empty())
    {
        DisplayCoords coords = getDisplayCoordsFromMessageType(messageType);

        mvprintw(coords.x, coords.y, message.c_str());
    }
}

DisplayCoords HUDManager::getDisplayCoordsFromMessageType(enum MessageType messageType)
{
    DisplayCoords coords;

    switch(messageType)
    {
        case UserInstruction:
            coords.x = 0;
            coords.y = 0;
            break;
        case HardwareStatus:
            coords.x = 0;
            coords.y = 1;
            break;
        case Telemetry:
            coords.x = 0;
            coords.y = 2;
            break;
    }

    return coords;
}
