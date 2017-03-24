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
        HUDManager::logMessage(Telemetry, "Insufficent memory.");
        returnValue = false;
    }

    // make connection...
    if (IS_FAIL(_driver->connect(opt_com_path, opt_com_baudrate)))
    {
        HUDManager::logMessage(Telemetry, "Error, cannot bind to the specified serial port.");

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

float LidarManager::IsObjectAhead(int thresholdInches)
{
    size_t nodeCount = _countof(_nodes);
    float totalDistanceOfQualityPoints = 0.0;
    float totalQualityPoints = 0.0;

    for (int pos = 0; pos < (int)nodeCount ; ++pos)
    {
        float angle = (_nodes[pos].angle_q6_checkbit >> RPLIDAR_RESP_MEASUREMENT_ANGLE_SHIFT)/64.0f;
        float distance = _nodes[pos].distance_q2/4.0f;
        float quality = _nodes[pos].sync_quality >> RPLIDAR_RESP_MEASUREMENT_QUALITY_SHIFT;

        float distanceInches = distance / MM_TO_INCH;

        if(distanceInches > 0 && distanceInches < 10 && quality > 0 && IsAheadOfVehicle(angle))
        {
            std::stringstream message;
            message << "Object detected " << std::to_string(distanceInches) << " ahead of vehicle";
            HUDManager::logMessage(Telemetry, message.str());

            totalDistanceOfQualityPoints += distanceInches;
            totalQualityPoints++;
        }
    }

    float averageDistance = (totalDistanceOfQualityPoints / totalQualityPoints);

    if(averageDistance <= thresholdInches)
    {
        return averageDistance;
    }

    return 0;
}

/*TODO:

1) Add method to return a 360 element array (or object) of booleans for each of the degrees that have an object 'near' the robot
2) Add method to paint a circle in the HUD to represent objects near the robot
3) Use 'fido' nn library to train using the 360 element array:
    a) 360 booleans (1/0) as inputs
    b) 4 outputs: Forward, Backward, RotateRight, RotateLeft
    c) First goal is to train the robot to turn a right corner, given that the robot is along the wall next to the corner
4) Training:
    a) Create inputmanager options to take 'actions': Forward 1 inch, Backward 1 inch, Rotate right X degrees, Rotate left X degrees
    b) Train by making moves manually and having the nn listen / train based on lidar data (360 degree array) and moves I make manually
    c) Train the corner over and over again until the weights stop changing
    d) Serialize nn into file to be read and used to make actual right turns
    e) Repeat for other driving type activities*/

float LidarManager::IsObjectBehind(int thresholdInches)
{
    size_t nodeCount = _countof(_nodes);
    float totalDistanceOfQualityPoints = 0.0;
    float totalQualityPoints = 0.0;

    for (int pos = 0; pos < (int)nodeCount ; ++pos)
    {
        float angle = (_nodes[pos].angle_q6_checkbit >> RPLIDAR_RESP_MEASUREMENT_ANGLE_SHIFT)/64.0f;
        float distance = _nodes[pos].distance_q2/4.0f;
        float quality = _nodes[pos].sync_quality >> RPLIDAR_RESP_MEASUREMENT_QUALITY_SHIFT;

        float distanceInches = distance / MM_TO_INCH;

        if(distanceInches > 0 && distanceInches < 200 && quality > 0 && IsBehindVehicle(angle))
        {
            std::stringstream message;
            message << "Object detected " << std::to_string(distanceInches) << " behind of vehicle";
            HUDManager::logMessage(Telemetry, message.str());

            totalDistanceOfQualityPoints += distanceInches;
            totalQualityPoints++;
        }
    }

    float averageDistance = (totalDistanceOfQualityPoints / totalQualityPoints);

    if(averageDistance <= thresholdInches)
    {
        return averageDistance;
    }

    return 0;
}

void LidarManager::PrintScanData()
{
    rplidar_response_measurement_node_t nodes[360*2];
    size_t   nodeCount = _countof(nodes);
    u_result     op_result;

    op_result = _driver->grabScanData(nodes, nodeCount);

    for (int pos = 0; pos < (int)nodeCount ; ++pos)
    {
        float angle = (nodes[pos].angle_q6_checkbit >> RPLIDAR_RESP_MEASUREMENT_ANGLE_SHIFT)/64.0f;
        float distance = nodes[pos].distance_q2/4.0f;
        float quality = nodes[pos].sync_quality >> RPLIDAR_RESP_MEASUREMENT_QUALITY_SHIFT;

        float distanceInches = distance / MM_TO_INCH;

        if(distanceInches > 0 && distanceInches < 10 && quality > 15)
        {
            std::stringstream message;
            message << "Object detected " << std::to_string(distanceInches) << " inches at angle " << std::to_string(angle) << " of vehicle at " << std::to_string(quality);
            HUDManager::logMessage(Telemetry, message.str());
        }
    }
}
unordered_map<int, bool> LidarManager::GetPerimeter()
{
    unordered_map<int, bool> seen;

    rplidar_response_measurement_node_t nodes[360*2];
    size_t   nodeCount = _countof(nodes);
    u_result     op_result;

    op_result = _driver->grabScanData(nodes, nodeCount);

    for (int pos = 0; pos < (int)nodeCount ; ++pos)
    {
        float angle = (nodes[pos].angle_q6_checkbit >> RPLIDAR_RESP_MEASUREMENT_ANGLE_SHIFT)/64.0f;
        float distance = nodes[pos].distance_q2/4.0f;
        float quality = nodes[pos].sync_quality >> RPLIDAR_RESP_MEASUREMENT_QUALITY_SHIFT;

        float distanceInches = distance / MM_TO_INCH;


        if(distanceInches > 0 && distanceInches < 10 && quality > 15)
        {
            if(!seen.at(angle))
            {
                seen.insert({ angle, true });
            }
        }
    }

    return seen;
}

bool LidarManager::IsAheadOfVehicle(float angle)
{
    return angle > 165 && angle < 195;
}

bool LidarManager::IsBehindVehicle(float angle)
{
    return angle > 0 && angle < 45;
}

bool LidarManager::CheckRPLIDARHealth(RPlidarDriver * drv)
{
    u_result     op_result;
    rplidar_response_device_health_t healthinfo;

    op_result = drv->getHealth(healthinfo);
    if (IS_OK(op_result))   // the macro IS_OK is the preperred way to judge whether the operation is succeed.
    {
        std::stringstream message;
        message << "RPLidar health status: " << std::to_string(healthinfo.status);
        HUDManager::logMessage(Telemetry, message.str());

        if (healthinfo.status == RPLIDAR_STATUS_ERROR)
        {
            HUDManager::logMessage(Telemetry, "Internal lidar error occurred. Please restart.");
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
        HUDManager::logMessage(Telemetry, "Unable to determine lidar health.");
        return false;
    }
}
