#ifndef FACETARGETPERCEPTRON_H
#define FACETARGETPERCEPTRON_H

#include <stdio.h>
#include <stdlib.h>
#include <vector>
#include <iostream>

using namespace std;

class FaceTargetPerceptron
{
public:
    FaceTargetPerceptron();
    virtual ~FaceTargetPerceptron();

    std::vector<float> FeedForward(std::vector<float> forces);
    void Train(std::vector<float> forces, std::vector<float> forceErrors);
    std::vector<float>* CalculateError(float currentTheta, std::vector<float> forces);
protected:
private:
    //Represents [Left Track Weight, Right Track Weight]
    std::vector<float> _forceWeights;

    float _learningConstant;
};

#endif // FACETARGETPERCEPTRON_H
