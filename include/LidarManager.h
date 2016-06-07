#ifndef LIDARMANAGER_H
#define LIDARMANAGER_H

#include <stdio.h>
#include <stdlib.h>
#include "rplidar.h" //RPLIDAR standard sdk, all-in-one header
#include <curses.h>

#ifndef _countof
#define _countof(_Array) (int)(sizeof(_Array) / sizeof(_Array[0]))
#endif

#define NODE_COUNT 360*2
#define MM_TO_INCH 25.4

using namespace rp::standalone::rplidar;

class LidarManager
{
public:
    LidarManager();
    virtual ~LidarManager();
    bool InitiateDataCollection();
    float IsObjectAhead(int thresholdInches);
    float IsObjectBehind(int thresholdInches);
    void FetchNewScanData();
protected:
private:
    bool CheckRPLIDARHealth(RPlidarDriver * drv);
    bool IsAheadOfVehicle(float angle);
    bool IsBehindVehicle(float angle);

    RPlidarDriver* _driver;
    rplidar_response_measurement_node_t _nodes[NODE_COUNT];
    int _navigationCount;
};

#endif // LIDARMANAGER_H
