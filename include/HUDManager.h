#ifndef HUDMANAGER_H
#define HUDMANAGER_H

#include <curses.h>
#include <string>

using namespace std;

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

class HUDManager
{
    public:
        HUDManager();
        virtual ~HUDManager();

        static void logMessage(enum MessageType messageType, std::string message);
        static DisplayCoords getDisplayCoordsFromMessageType(enum MessageType messageType);
    protected:
    private:
        static bool initialized;
        static void initialize();
};



#endif // HUDMANAGER_H
