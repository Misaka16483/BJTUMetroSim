package com.bjtu.metro;

import java.util.List;

public final class GradeProfile {
    private final List<GradeSegment> segments;

    public GradeProfile(List<GradeSegment> segments) {
        this.segments = List.copyOf(segments);
    }

    public double gradeAt(double positionMeters) {
        for (GradeSegment segment : segments) {
            if (segment.contains(positionMeters)) {
                return segment.gradePromille();
            }
        }
        return 0.0;
    }
}
