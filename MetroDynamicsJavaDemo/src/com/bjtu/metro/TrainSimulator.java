package com.bjtu.metro;

import java.util.ArrayList;
import java.util.List;

public final class TrainSimulator {
    private static final double GRAVITY = 9.80665;

    private final TrainParameters params;
    private final GradeProfile gradeProfile;

    public TrainSimulator(TrainParameters params, GradeProfile gradeProfile) {
        this.params = params;
        this.gradeProfile = gradeProfile;
    }

    public List<SimulationPoint> simulate(List<Station> stations) {
        List<SimulationPoint> points = new ArrayList<>();
        double time = 0.0;
        double speed = 0.0;
        double tractionEnergyKWh = 0.0;
        double regenEnergyKWh = 0.0;
        double auxiliaryEnergyKWh = 0.0;

        for (int i = 0; i < stations.size() - 1; i++) {
            Station from = stations.get(i);
            Station to = stations.get(i + 1);
            double position = from.mileageMeters();
            double target = to.mileageMeters();
            double sectionLimit = Math.min(params.maxSpeedMetersPerSecond(), from.speedLimitToNextKmh() / 3.6);

            while (position < target || speed > 0.01) {
                double remaining = Math.max(0.0, target - position);
                if (remaining < 0.5 && speed < 0.1) {
                    position = target;
                    speed = 0.0;
                    points.add(new SimulationPoint(
                            time,
                            position,
                            speed,
                            0.0,
                            gradeProfile.gradeAt(position),
                            0.0,
                            0.0,
                            0.0,
                            tractionEnergyKWh,
                            regenEnergyKWh,
                            auxiliaryEnergyKWh,
                            "ARRIVE",
                            from.name(),
                            to.name()
                    ));
                    break;
                }

                double gradePromille = gradeProfile.gradeAt(position);
                double resistanceForce = params.runningResistanceN(speed);
                double gradeForce = params.trainMassKg() * GRAVITY * gradePromille / 1000.0;
                double brakeDeceleration = estimateBrakeDeceleration(speed, resistanceForce, gradeForce);
                double brakingDistance = speed * speed / (2.0 * brakeDeceleration);

                double tractionForce = 0.0;
                double brakeForce = 0.0;
                String phase;
                if (speed > 0.1 && remaining <= brakingDistance + 1.0) {
                    brakeForce = params.maxServiceBrakeForceN();
                    phase = "BRAKE";
                } else if (speed < sectionLimit - 0.05) {
                    tractionForce = availableTractionForce(speed);
                    phase = "ACCEL";
                } else {
                    double holdForce = resistanceForce + gradeForce;
                    if (holdForce >= 0) {
                        tractionForce = Math.min(availableTractionForce(speed), holdForce);
                    } else {
                        brakeForce = Math.min(params.maxServiceBrakeForceN(), -holdForce);
                    }
                    phase = "CRUISE";
                }

                double dt = params.timeStepSeconds();
                double netForce = tractionForce - brakeForce - resistanceForce - gradeForce;
                double acceleration = netForce / params.equivalentMassKg();
                double nextSpeed = Math.max(0.0, Math.min(sectionLimit, speed + acceleration * dt));
                acceleration = (nextSpeed - speed) / dt;
                double averageSpeed = (speed + nextSpeed) / 2.0;
                double nextPosition = position + averageSpeed * dt;

                tractionEnergyKWh += tractionForce * averageSpeed * dt / 3_600_000.0;
                regenEnergyKWh += regeneratedEnergyKWh(brakeForce, averageSpeed, dt);
                auxiliaryEnergyKWh += params.auxiliaryPowerW() * dt / 3_600_000.0;

                if (nextPosition >= target && phase.equals("BRAKE")) {
                    double actualDt = speed > 0.01 ? Math.max(0.1, remaining / Math.max(0.01, averageSpeed)) : dt;
                    double usedDt = Math.min(dt, actualDt);
                    time += usedDt;
                    position = target;
                    speed = 0.0;
                    points.add(new SimulationPoint(
                            time,
                            position,
                            speed,
                            acceleration,
                            gradePromille,
                            tractionForce,
                            brakeForce,
                            resistanceForce,
                            tractionEnergyKWh,
                            regenEnergyKWh,
                            auxiliaryEnergyKWh,
                            "ARRIVE",
                            from.name(),
                            to.name()
                    ));
                    break;
                }

                time += dt;
                position = Math.min(nextPosition, target);
                speed = nextSpeed;
                points.add(new SimulationPoint(
                        time,
                        position,
                        speed,
                        acceleration,
                        gradePromille,
                        tractionForce,
                        brakeForce,
                        resistanceForce,
                        tractionEnergyKWh,
                        regenEnergyKWh,
                        auxiliaryEnergyKWh,
                        phase,
                        from.name(),
                        to.name()
                ));
            }

            if (to.dwellSeconds() > 0) {
                auxiliaryEnergyKWh += params.auxiliaryPowerW() * to.dwellSeconds() / 3_600_000.0;
                time += to.dwellSeconds();
                points.add(new SimulationPoint(
                        time,
                        to.mileageMeters(),
                        0.0,
                        0.0,
                        gradeProfile.gradeAt(to.mileageMeters()),
                        0.0,
                        0.0,
                        0.0,
                        tractionEnergyKWh,
                        regenEnergyKWh,
                        auxiliaryEnergyKWh,
                        "DWELL",
                        to.name(),
                        to.name()
                ));
            }
        }

        return points;
    }

    private double availableTractionForce(double speedMetersPerSecond) {
        double powerLimitedForce = params.maxTractionPowerW() / Math.max(1.0, speedMetersPerSecond);
        return Math.min(params.maxTractionForceN(), powerLimitedForce);
    }

    private double estimateBrakeDeceleration(double speedMetersPerSecond, double resistanceForce, double gradeForce) {
        double force = params.maxServiceBrakeForceN() + resistanceForce + gradeForce;
        double deceleration = force / params.equivalentMassKg();
        return Math.max(0.35, deceleration);
    }

    private double regeneratedEnergyKWh(double brakeForce, double averageSpeed, double dt) {
        if (brakeForce <= 0 || averageSpeed <= 0) {
            return 0.0;
        }
        double brakePower = brakeForce * averageSpeed;
        double usablePower = Math.min(brakePower, params.maxRegenPowerW());
        return usablePower * params.regenEfficiency() * dt / 3_600_000.0;
    }
}
