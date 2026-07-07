package com.bjtu.metro;

public record SimulationPoint(
        double timeSeconds,
        double positionMeters,
        double speedMetersPerSecond,
        double accelerationMetersPerSecond2,
        double gradePromille,
        double tractionForceN,
        double brakeForceN,
        double resistanceForceN,
        double tractionEnergyKWh,
        double regenEnergyKWh,
        double auxiliaryEnergyKWh,
        String phase,
        String fromStation,
        String toStation
) {
    public String toCsvLine() {
        return String.format(
                "%.1f,%.3f,%.3f,%.3f,%.3f,%.3f,%.1f,%.1f,%.1f,%.5f,%.5f,%.5f,%.5f,%s,%s,%s",
                timeSeconds,
                positionMeters,
                speedMetersPerSecond,
                speedMetersPerSecond * 3.6,
                accelerationMetersPerSecond2,
                gradePromille,
                tractionForceN,
                brakeForceN,
                resistanceForceN,
                tractionEnergyKWh,
                regenEnergyKWh,
                auxiliaryEnergyKWh,
                tractionEnergyKWh + auxiliaryEnergyKWh - regenEnergyKWh,
                phase,
                fromStation,
                toStation
        );
    }
}
