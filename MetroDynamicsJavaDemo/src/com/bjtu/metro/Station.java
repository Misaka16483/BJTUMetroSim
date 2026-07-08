package com.bjtu.metro;

public record Station(
        int id,
        String code,
        String name,
        double mileageMeters,
        double speedLimitToNextKmh,
        double dwellSeconds
) {
}
