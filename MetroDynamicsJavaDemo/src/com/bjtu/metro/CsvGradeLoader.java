package com.bjtu.metro;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

public final class CsvGradeLoader {
    private CsvGradeLoader() {
    }

    public static GradeProfile load(Path path) throws IOException {
        if (!Files.exists(path)) {
            return new GradeProfile(List.of());
        }
        List<String> lines = Files.readAllLines(path, StandardCharsets.UTF_8);
        List<GradeSegment> segments = new ArrayList<>();
        for (int i = 1; i < lines.size(); i++) {
            String line = lines.get(i).trim();
            if (line.isEmpty()) {
                continue;
            }
            String[] cells = line.split(",", -1);
            if (cells.length < 3) {
                throw new IllegalArgumentException("坡度 CSV 第 " + (i + 1) + " 行字段不足: " + line);
            }
            segments.add(new GradeSegment(
                    Double.parseDouble(cells[0]),
                    Double.parseDouble(cells[1]),
                    Double.parseDouble(cells[2])
            ));
        }
        return new GradeProfile(segments);
    }
}
