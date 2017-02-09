#ifndef LOCATIONMANAGER_H
#define LOCATIONMANAGER_H

#include "libgpsmm.h"

using namespace std;

class LocationManager
{
    public:
        LocationManager();
        virtual ~LocationManager();
        void ReadState();
        void PrintState(struct gps_data_t *collect);
    protected:
    private:
        gpsmm* _gpsDriver;
};

#endif // LOCATIONMANAGER_H
