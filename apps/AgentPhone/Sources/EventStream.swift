import Foundation

/// Server-Sent Events reader for /api/events. Yields decoded events and
/// reconnects on its own, so the UI always reflects the daemon.
final class EventStream: NSObject, URLSessionDataDelegate {
    private var session: URLSession!
    private var task: URLSessionDataTask?
    private var buffer = Data()
    private let onEvent: (Event) -> Void
    private let onConnalive: (Bool) -> Void
    private var urlProvider: () -> URL
    private var stopped = false

    init(url: @escaping () -> URL, onEvent: @escaping (Event) -> Void, onConnection: @escaping (Bool) -> Void) {
        self.urlProvider = url
        self.onEvent = onEvent
        self.onConnalive = onConnection
        super.init()
        session = URLSession(configuration: .default, delegate: self, delegateQueue: nil)
    }

    func start() {
        stopped = false
        connect()
    }

    func stop() {
        stopped = true
        task?.cancel()
    }

    private func connect() {
        var req = URLRequest(url: urlProvider())
        req.timeoutInterval = .infinity
        req.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        task = session.dataTask(with: req)
        task?.resume()
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        DispatchQueue.main.async { self.onConnalive(true) }
        buffer.append(data)
        while let range = buffer.range(of: Data("\n\n".utf8)) {
            let chunk = buffer.subdata(in: buffer.startIndex..<range.lowerBound)
            buffer.removeSubrange(buffer.startIndex..<range.upperBound)
            parse(chunk)
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        DispatchQueue.main.async { self.onConnalive(false) }
        guard !stopped else { return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 2) { [weak self] in self?.connect() }
    }

    private func parse(_ chunk: Data) {
        for line in String(decoding: chunk, as: UTF8.self).split(separator: "\n") {
            guard line.hasPrefix("data:") else { continue }
            let json = line.dropFirst(5).trimmingCharacters(in: .whitespaces)
            if let d = json.data(using: .utf8), let ev = try? JSONDecoder().decode(Event.self, from: d) {
                DispatchQueue.main.async { self.onEvent(ev) }
            }
        }
    }
}
