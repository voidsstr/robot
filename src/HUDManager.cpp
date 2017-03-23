#include "HUDManager.h"

bool HUDManager::initialized = false;

HUDManager::HUDManager()
{

}

HUDManager::~HUDManager()
{
    //dtor
}

void HUDManager::DrawCircle(int originX, int originY, int width, int height, std::vector<int> degrees)
{
    float deg;
	int y, x;

    /* Draw circle */
	for (deg = 0; deg < 360.0f; deg += 1.0f)
	{
        if(degrees.at(deg) == 1)
        {
            x = originX + width+(int)(width * cos(DEGTORAD(deg)));
            y = originY + height+(int)(height * sin(DEGTORAD(deg)));

            mvaddch(y,x,DOT);
		}
	}
}

void HUDManager::initialize()
{
    WINDOW *w = initscr();
    cbreak();
    nodelay(w, TRUE);
    raw();
    keypad(stdscr, TRUE);
    noecho();

    HUDManager().initialized = true;
}

void HUDManager::logMessage(enum MessageType messageType, std::string message)
{
    if(!HUDManager().initialized)
    {
        initialize();
    }

    if(!message.empty())
    {
        DisplayCoords coords = getDisplayCoordsFromMessageType(messageType);

        //Clear line
        move(coords.y, 0);
        clrtoeol();

        mvprintw(coords.y, coords.x, message.c_str());

        //Force printing (not sure why this works)
        getch();
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
        case InputFeedback:
            coords.x = 0;
            coords.y = 3;
            break;
    }

    return coords;
}
