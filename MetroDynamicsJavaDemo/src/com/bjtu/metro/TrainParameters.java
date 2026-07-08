package com.bjtu.metro;

public record TrainParameters(
        double maxSpeedKmh,
        double trainMassKg,
        double rotatingMassFactor,
        double maxTractionForceN,
        double maxTractionPowerW,
        double maxServiceBrakeForceN,
        double maxRegenPowerW,
        double regenEfficiency,
        double auxiliaryPowerW,
        double resistanceA,
        double resistanceB,
        double resistanceC,
        double timeStepSeconds
) {
    public double maxSpeedMetersPerSecond() {
        return maxSpeedKmh / 3.6;
    }

    public double equivalentMassKg() {
        return trainMassKg * rotatingMassFactor;
    }

    public double runningResistanceN(double speedMetersPerSecond) {
        return resistanceA + resistanceB * speedMetersPerSecond + resistanceC * speedMetersPerSecond * speedMetersPerSecond;
    }
}
