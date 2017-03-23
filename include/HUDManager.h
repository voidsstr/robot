#ifndef HUDMANAGER_H
#define HUDMANAGER_H

#include <curses.h>
#include <string>
#include <vector>
#include <math.h>

using namespace std;

#define DOT 46

enum MessageType
{
    Telemetry,
    HardwareStatus,
    UserInstruction,
    InputFeedback
};

struct DisplayCoords
{
    int x;
    int y;
};

#define DEGTORAD(deg) (deg * (180.0f/M_PI))

class HUDManager
{
    public:
        HUDManager();
        virtual ~HUDManager();

        static void logMessage(enum MessageType messageType, std::string message);
        static DisplayCoords getDisplayCoordsFromMessageType(enum MessageType messageType);
        static void DrawCircle(int originX, int originY, int width, int height, std::vector<int> degrees);
    protected:
    private:
        static bool initialized;
        static void initialize();
};



#endif // HUDMANAGER_H
