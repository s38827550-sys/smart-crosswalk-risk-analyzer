import cv2


def is_point_in_polygon(point, polygon):
    result = cv2.pointPolygonTest(polygon, point, False)
    return result >= 0


def calculate_vehicle_proximity(vehicle_box, crosswalk_polygon, frame_w, frame_h):
    bx1, by1, bx2, by2 = vehicle_box

    check_points = [
        (bx1, by1),
        (bx2, by1),
        (bx1, by2),
        (bx2, by2),
        ((bx1 + bx2) // 2, (by1 + by2) // 2),
        ((bx1 + bx2) // 2, by2),
    ]

    for point in check_points:
        if cv2.pointPolygonTest(crosswalk_polygon, point, False) >= 0:
            return 100

    min_distance = float("inf")

    for point in check_points:
        distance = cv2.pointPolygonTest(crosswalk_polygon, point, True)
        min_distance = min(min_distance, abs(distance))

    max_distance = max(frame_w, frame_h) * 0.45
    proximity = 100 - int((min_distance / max_distance) * 100)
    proximity = max(0, min(100, proximity))

    return proximity


def calculate_person_vehicle_risk(vehicle_point, person_point, frame_w, frame_h):
    vx, vy = vehicle_point
    px, py = person_point

    distance = ((vx - px) ** 2 + (vy - py) ** 2) ** 0.5
    max_distance = max(frame_w, frame_h) * 0.25

    risk = 100 - int((distance / max_distance) * 100)
    risk = max(0, min(100, risk))

    return risk, distance


def get_vehicle_front_point(bx1, by1, bx2, by2, current_center, prev_center):
    if prev_center is None:
        return bx1, (by1 + by2) // 2

    dx = current_center[0] - prev_center[0]
    dy = current_center[1] - prev_center[1]

    if abs(dx) > abs(dy):
        if dx > 0:
            return bx2, (by1 + by2) // 2
        else:
            return bx1, (by1 + by2) // 2
    else:
        if dy > 0:
            return (bx1 + bx2) // 2, by2
        else:
            return (bx1 + bx2) // 2, by1