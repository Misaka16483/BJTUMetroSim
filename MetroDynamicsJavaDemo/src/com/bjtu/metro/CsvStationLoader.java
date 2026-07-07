package com.bjtu.metro;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

public final class CsvStationLoader {
    private CsvStationLoader() {
    }

    public static List<Station> load(Path path) throws IOException {
        List<String> lines = Files.readAllLines(path, StandardCharsets.UTF_8);
        List<Station> stations = new ArrayList<>();
        for (int i = 1; i < lines.size(); i++) {
            String line = lines.get(i).trim();
            if (line.isEmpty()) {
                continue;
            }
            String[] cells = line.split(",", -1);
            if (cells.length < 6) {
                throw new IllegalArgumentException("CSV 第 " + (i + 1) + " 行字段不足: " + line);
            }
            stations.add(new Station(
                    Integer.parseInt(cells[0]),
                    cells[1],
                    cells[2],
                    Double.parseDouble(cells[3]),
                    Double.parseDouble(cells[4]),
                    Double.parseDouble(cells[5])
            ));
        }
        if (stations.size() < 2) {
            throw new IllegalArgumentException("至少需要两个车站才能仿真");
        }
        return stations;
    }
}
