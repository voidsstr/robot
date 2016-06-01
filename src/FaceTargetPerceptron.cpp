#include "FaceTargetPerceptron.h"

FaceTargetPerceptron::FaceTargetPerceptron()
{
    //Init left and right track force weights to random values
    _forceWeights.push_back(rand() % 100 * 0.01);
    _forceWeights.push_back(rand() % 100 * 0.01);
}

FaceTargetPerceptron::~FaceTargetPerceptron()
{
    //dtor
}

std::vector<float> FaceTargetPerceptron::FeedForward(std::vector<float> forces)
{
    std::vector<float> summedForces;

    int index = 0;
    for(float force : forces)
    {
        summedForces.push_back(force * _forceWeights.at(index));
        index++;
    }

    return summedForces;
}

void FaceTargetPerceptron::Train(std::vector<float> forces, std::vector<float> forceErrors)
{

}

std::vector<float>* FaceTargetPerceptron::CalculateError(float currentTheta, std::vector<float> forces)
{
    float desiredLeftToRightRatio;

    if(currentTheta > 0 && currentTheta < 180)
    {
        //Ratio should be 2:1
        desiredLeftToRightRatio = 2.0/3.0;
    }
    else
    {
        //Ratio should be 1:2
        desiredLeftToRightRatio = 1.0/3.0;
    }

    //Left force / total forces between right and left
    float currentLeftToRightRatio = forces.at(0) / (forces.at(0) + forces.at(1));

    std::vector<float>* errors = new std::vector<float>();

    errors->push_back(desiredLeftToRightRatio - currentLeftToRightRatio);
    errors->push_back(currentLeftToRightRatio - desiredLeftToRightRatio);
}
