#include "LidarManager.h"

LidarManager::LidarManager()
{
    //ctor
}

LidarManager::~LidarManager()
{
    //dtor
    RPlidarDriver::DisposeDriver(_driver);
}

bool LidarManager::InitiateDataCollection()
{
    bool returnValue = true;

    const char * opt_com_path = NULL;
    _u32         opt_com_baudrate = 115200;

    if (!opt_com_path) {
#ifdef _WIN32
        // use default com port
        opt_com_path = "\\\\.\\com3";
#else
        opt_com_path = "/dev/ttyUSB0";
#endif
    }

    // create the driver instance
    _driver = RPlidarDriver::CreateDriver(RPlidarDriver::DRIVER_TYPE_SERIALPORT);

    if (!_driver) {
        fprintf(stderr, "insufficent memory, exit\n");
        returnValue = false;
    }

    // make connection...
    if (IS_FAIL(_driver->connect(opt_com_path, opt_com_baudrate))) {
        fprintf(stderr, "Error, cannot bind to the specified serial port %s.\n"
            , opt_com_path);

        returnValue = false;
    }

    // check health...
    if (!CheckRPLIDARHealth(_driver)) {
        returnValue = false;
    }

    // start scan...
    _driver->startScan();

    return returnValue;
}

bool LidarManager::IsObjectAhead(int thresholdInches)
{
    u_result op_result;

    rplidar_response_measurement_node_t nodes[360*2];
    size_t   count = _countof(nodes);

    op_result = _driver->grabScanData(nodes, count);

    if (IS_OK(op_result)) {
        _driver->ascendScanData(nodes, count);

        for (int pos = 0; pos < (int)count ; ++pos) {
            float angle = (nodes[pos].angle_q6_checkbit >> RPLIDAR_RESP_MEASUREMENT_ANGLE_SHIFT)/64.0f;
            float distance = nodes[pos].distance_q2/4.0f;
            float quality = nodes[pos].sync_quality >> RPLIDAR_RESP_MEASUREMENT_QUALITY_SHIFT;

            if(IsAheadOfVehicle(angle) && distance <= (thresholdInches * 25.4))
            {
                return true;
            }
        }
    }

    return false;
}

bool LidarManager::IsAheadOfVehicle(float angle)
{
    return angle > 165 && angle < 195;
}

bool LidarManager::CheckRPLIDARHealth(RPlidarDriver * drv)
{
    u_result     op_result;
    rplidar_response_device_health_t healthinfo;


    op_result = drv->getHealth(healthinfo);
    if (IS_OK(op_result)) { // the macro IS_OK is the preperred way to judge whether the operation is succeed.
        printf("RPLidar health status : %d\n", healthinfo.status);
        if (healthinfo.status == RPLIDAR_STATUS_ERROR) {
            fprintf(stderr, "Error, rplidar internal error detected. Please reboot the device to retry.\n");
            // enable the following code if you want rplidar to be reboot by software
            // drv->reset();
            return false;
        } else {
            return true;
        }

    } else {
        fprintf(stderr, "Error, cannot retrieve the lidar health code: %x\n", op_result);
        return false;
    }
}
