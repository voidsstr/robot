#include "LidarManager.h"

LidarManager::LidarManager()
{
    //ctor
    _navigationCount = 0;
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

    if (!opt_com_path)
    {
#ifdef _WIN32
        // use default com port
        opt_com_path = "\\\\.\\com3";
#else
        opt_com_path = "/dev/ttyUSB0";
#endif
    }

    // create the driver instance
    _driver = RPlidarDriver::CreateDriver(RPlidarDriver::DRIVER_TYPE_SERIALPORT);

    if (!_driver)
    {
        fprintf(stderr, "insufficent memory, exit\n");
        returnValue = false;
    }

    // make connection...
    if (IS_FAIL(_driver->connect(opt_com_path, opt_com_baudrate)))
    {
        fprintf(stderr, "Error, cannot bind to the specified serial port %s.\n"
                , opt_com_path);

        returnValue = false;
    }

    // check health...
    if (!CheckRPLIDARHealth(_driver))
    {
        returnValue = false;
    }

    // start scan...
    _driver->startScan();

    return returnValue;
}

void LidarManager::FetchNewScanData()
{
    u_result op_result;

    size_t nodeCount = _countof(_nodes);

    op_result = _driver->grabScanData(_nodes, nodeCount);

    if (IS_OK(op_result))
    {
        _driver->ascendScanData(_nodes, nodeCount);
    }

}

bool LidarManager::IsObjectAhead(int thresholdInches)
{
    rplidar_response_measurement_node_t nodes[360*2];
    size_t   nodeCount = _countof(nodes);
    u_result     op_result;

    op_result = _driver->grabScanData(nodes, nodeCount);

    for (int pos = 0; pos < (int)nodeCount ; ++pos)
    {
        float angle = (nodes[pos].angle_q6_checkbift >> RPLIDAR_RESP_MEASUREMENT_ANGLE_SHIFT)/64.0f;
        float distance = nodes[pos].distance_q2/4.0f;
        float quality = nodes[pos].sync_quality >> RPLIDAR_RESP_MEASUREMENT_QUALITY_SHIFT;

        float distanceInches = distance / MM_TO_INCH;

        if(distanceInches > 0 && distanceInches < 10 && quality > 15 && IsAheadOfVehicle(angle))
        {
            return true;
        }
    }

    return false;
}

bool LidarManager::IsObjectBehind(int thresholdInches)
{
    rplidar_response_measurement_node_t nodes[360*2];
    size_t   nodeCount = _countof(nodes);
    u_result     op_result;

    op_result = _driver->grabScanData(nodes, nodeCount);

    for (int pos = 0; pos < (int)nodeCount ; ++pos)
    {
        float angle = (nodes[pos].angle_q6_checkbift >> RPLIDAR_RESP_MEASUREMENT_ANGLE_SHIFT)/64.0f;
        float distance = nodes[pos].distance_q2/4.0f;
        float quality = nodes[pos].sync_quality >> RPLIDAR_RESP_MEASUREMENT_QUALITY_SHIFT;

        float distanceInches = distance / MM_TO_INCH;

        if(distanceInches > 0 && distanceInches < 10 && quality > 15 && IsBehindVehicle(angle))
        {
            return true;
        }
    }

    return false;
}

void LidarManager::PrintScanData()
{
    rplidar_response_measurement_node_t nodes[360*2];
    size_t   nodeCount = _countof(nodes);
    u_result     op_result;

    op_result = _driver->grabScanData(nodes, nodeCount);

    for (int pos = 0; pos < (int)nodeCount ; ++pos)
    {
        float angle = (nodes[pos].angle_q6_checkbift >> RPLIDAR_RESP_MEASUREMENT_ANGLE_SHIFT)/64.0f;
        float distance = nodes[pos].distance_q2/4.0f;
        float quality = nodes[pos].sync_quality >> RPLIDAR_RESP_MEASUREMENT_QUALITY_SHIFT;

        float distanceInches = distance / MM_TO_INCH;

        if(distanceInches > 0 && distanceInches < 10 && quality > 15)
        {
            clear();
            mvprintw(0, 0, "Object detected %f inches at %f angle of %f quality", distanceInches, angle, quality);
            refresh();
        }
    }
}

bool LidarManager::IsAheadOfVehicle(float angle)
{
    return (angle >= 180 && angle =< 225) || (angle <= 180 && angle >= 135);
}

bool LidarManager::IsBehindVehicle(float angle)
{
    return (angle >= 0 && angle =< 45) || (angle <= 360 && angle >= 315);
}

bool LidarManager::CheckRPLIDARHealth(RPlidarDriver * drv)
{
    u_result     op_result;
    rplidar_response_device_health_t healthinfo;

    op_result = drv->getHealth(healthinfo);
    if (IS_OK(op_result))   // the macro IS_OK is the preperred way to judge whether the operation is succeed.
    {
        printf("RPLidar health status : %d\n", healthinfo.status);
        if (healthinfo.status == RPLIDAR_STATUS_ERROR)
        {
            fprintf(stderr, "Error, rplidar internal error detected. Please reboot the device to retry.\n");
            // enable the following code if you want rplidar to be reboot by software
            drv->reset();
            return false;
        }
        else
        {
            return true;
        }

    }
    else
    {
        fprintf(stderr, "Error, cannot retrieve the lidar health code: %x\n", op_result);
        return false;
    }
}
