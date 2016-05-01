#ifndef LIDARMANAGER_H
#define LIDARMANAGER_H

#include <stdio.h>
#include <stdlib.h>
#include "rplidar.h" //RPLIDAR standard sdk, all-in-one header

#ifndef _countof
#define _countof(_Array) (int)(sizeof(_Array) / sizeof(_Array[0]))
#endif

using namespace rp::standalone::rplidar;

class LidarManager
{
    public:
        LidarManager();
        virtual ~LidarManager();
        void InitiateDataCollection();
    protected:
    private:
        bool CheckRPLIDARHealth(RPlidarDriver * drv);
        bool IsAheadOfVehicle(float angle);
};

#endif // LIDARMANAGER_H
