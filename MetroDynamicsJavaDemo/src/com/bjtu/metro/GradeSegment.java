package com.bjtu.metro;

public record GradeSegment(
        double startMeters,
        double endMeters,
        double gradePromille
) {
    public boolean contains(double positionMeters) {
        return positionMeters >= startMeters && positionMeters < endMeters;
    }
}
