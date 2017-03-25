#include "HUDManager.h"

bool HUDManager::initialized = false;

HUDManager::HUDManager()
{

}

HUDManager::~HUDManager()
{
    //dtor
}

void HUDManager::DrawLidarMap(int originX, int originY, int radius, unordered_map<int, float> perimiter, bool drawLabels, int distanceThreshold, bool includeDepth)
{
    DrawLidarMap(originX, originY, radius, perimiter, drawLabels, distanceThreshold);

    if(includeDepth)
    {
        do
        {
            distanceThreshold -= 2;
            radius -= 1;

            DrawLidarMap(originX, originY, radius, perimiter, false, distanceThreshold);
        }
        while(radius > 1);
    }
}

void HUDManager::DrawLidarMap(int originX, int originY, int radius, unordered_map<int, float> perimiter, bool drawLabels, int distanceThreshold)
{
    float deg;
	int y, x;
    float totalDeg = 0;
    /* Draw circle */
	for (deg = 0; deg < 360.0f; deg += 1.0f)
	{
        x = (radius * 1.65) * cos(deg * M_PI / 180.0f) + originX;
        y = (radius * 0.8) * sin(deg * M_PI / 180.0f) + originY;

        if(deg == 90)
        {
            y--;
        }

        if(drawLabels)
        {
            if(deg == 180)
            {
                mvprintw(y, x - 5, "REAR");
            }
            else if(deg == 0)
            {
                mvprintw(y, x + 2, "FRONT");
            }
            else if(deg == 270)
            {
                mvprintw(y - 1, x - 4, "LIDAR MAP");
            }
        }

        if(perimiter.find(deg) != perimiter.end() && perimiter.at(deg) <= distanceThreshold)
        {
            totalDeg += deg;
            attron(COLOR_PAIR(1));
            mvprintw(y, x, ".");
            attroff(COLOR_PAIR(1));
		}
		else
		{
            attron(COLOR_PAIR(2));
            mvprintw(y, x, ".");
            attroff(COLOR_PAIR(2));
		}

		refresh();
	}

	totalDeg = 0;
}

void HUDManager::initialize()
{
    WINDOW *w = initscr();
    cbreak();
    nodelay(w, TRUE);
    raw();
    keypad(stdscr, TRUE);
    noecho();
    curs_set(FALSE);
    start_color();

    init_pair(1, COLOR_RED, COLOR_BLACK);
    init_pair(2, COLOR_GREEN, COLOR_BLACK);

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
